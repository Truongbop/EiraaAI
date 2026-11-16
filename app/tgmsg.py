"""Tin nhắn nhóm Telegram: lưu trữ, tổng hợp, tóm tắt, phân loại, trích đầu việc / tiến độ / vấn đề."""
import json
import logging
import re
from datetime import datetime, time, timedelta

from . import ai, db, summary
from .config import VN_TZ

log = logging.getLogger("eiraa.tgmsg")
UNKNOWN = "Chưa xác định"


# ======================= Lưu trữ =======================
def upsert_chat(chat_id, title, ctype, active=True):
    ts = db.now().isoformat()
    with db.conn() as c:
        c.execute("""INSERT INTO tg_chats(chat_id,title,type,active,added_at,updated_at) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type,
                       active=excluded.active, updated_at=excluded.updated_at""",
                  (chat_id, title, ctype, int(active), ts, ts))


def save_message(chat_id, message_id, user_id, user_name, text, sent_at: datetime, kind="text", file_name=None):
    with db.conn() as c:
        c.execute("""INSERT OR IGNORE INTO tg_messages(chat_id,message_id,user_id,user_name,kind,text,file_name,sent_at)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (chat_id, message_id, user_id, user_name, kind, text, file_name, sent_at.astimezone(VN_TZ).isoformat()))


def list_chats():
    with db.conn() as c:
        return db.rows(c, """SELECT ch.*, COUNT(m.id) AS total, MAX(m.sent_at) AS last_at
                             FROM tg_chats ch LEFT JOIN tg_messages m ON m.chat_id=ch.chat_id
                             GROUP BY ch.chat_id ORDER BY last_at DESC""")


def get_messages(start, end, chat_ids=None):
    s = datetime.combine(start, time.min, VN_TZ).isoformat()
    e = datetime.combine(end, time.max, VN_TZ).isoformat()
    sql = """SELECT m.*, ch.title AS chat_title FROM tg_messages m LEFT JOIN tg_chats ch ON ch.chat_id=m.chat_id
             WHERE m.sent_at BETWEEN ? AND ?"""
    args = [s, e]
    if chat_ids:
        sql += f" AND m.chat_id IN ({','.join('?' * len(chat_ids))})"
        args += list(chat_ids)
    with db.conn() as c:
        return db.rows(c, sql + " ORDER BY m.sent_at", args)


def count_messages():
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM tg_messages").fetchone()[0]


def find_chats(text):
    """Tìm nhóm được nhắc tên trong câu hỏi, vd 'nhóm kinh doanh'."""
    t = (text or "").lower()
    found = []
    for ch in list_chats():
        title = (ch["title"] or "").lower()
        words = [w for w in re.split(r"\W+", title) if len(w) >= 3 and w not in ("nhóm", "phòng", "team", "group", "công", "ban")]
        if title and (title in t or any(w in t for w in words)):
            found.append(ch["chat_id"])
    return found


# ======================= Phân tích không cần AI =======================
CATEGORIES = {
    "Kinh doanh / Khách hàng": ["khách", "hợp đồng", "báo giá", "đơn hàng", "doanh số", "chốt", "sale", "chiết khấu"],
    "Kỹ thuật / Sản phẩm": ["lỗi", "bug", "deploy", "server", "code", "tính năng", "app", "web", "hệ thống", "kiểm thử", "test",
                            "đăng nhập", "máy chủ", "production", "thiết kế"],
    "Tài chính / Kế toán": ["thanh toán", "hóa đơn", "chi phí", "công nợ", "ngân sách", "thuế", "tiền", "đối soát"],
    "Nhân sự / Hành chính": ["tuyển", "phỏng vấn", "nghỉ phép", "chấm công", "lương", "nhân sự", "ứng viên"],
    "Marketing": ["quảng cáo", "facebook", "khuyến mãi", "bài viết", "chiến dịch", "marketing", "kol", "fanpage"],
    "Họp / Lịch làm việc": ["họp", "lịch", "meeting", "hẹn"],
}
TASK_RE = re.compile(r"\b(cần|phải|nhờ|giao|làm|hoàn thành|gửi|deadline|hạn|trước|xử lý|chuẩn bị|kiểm tra|liên hệ|theo dõi|xong|đang)\b", re.I)
DONE_RE = re.compile(r"\b(đã xong|xong rồi|hoàn thành|đã gửi|đã fix|đã xử lý|done|đã ký|đã chốt|đã thanh toán|xong)\b", re.I)
DOING_RE = re.compile(r"\b(đang|\d{1,3}\s?%)", re.I)
ISSUE_RE = re.compile(r"\b(lỗi|vấn đề|chậm|trễ|gấp|khẩn|không được|hỏng|sự cố|vướng|khó khăn|phàn nàn|quá hạn|chưa nhận|thiếu|bị giữ|không thanh toán)\b", re.I)
HIGH_RE = re.compile(r"\b(gấp|khẩn|sự cố|nghiêm trọng|không thanh toán|sập|mất dữ liệu)\b", re.I)
PROPOSAL_RE = re.compile(r"\b(đề xuất|xin phép|xin duyệt|kiến nghị|em nghĩ nên|nên)\b", re.I)
DEADLINE_RE = re.compile(r"(trước\s+)?(\d{1,2}h(?:\d{2})?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|hôm nay|ngày mai|mai|cuối tuần|cuối tháng|thứ\s?(?:hai|ba|tư|năm|sáu|bảy|[2-7]))", re.I)
PCT_RE = re.compile(r"(\d{1,3})\s?%")
MENTION_RE = re.compile(r"@(\w+)")
QUESTION_ONLY = re.compile(r"^(ok|oke|vâng|dạ|ừ|uh|👍|cảm ơn|thanks|tks)[\s!.]*$", re.I)


def _category(t):
    """Chọn nhóm công việc có nhiều từ khóa khớp nhất."""
    scores = {k: sum(1 for w in ws if w in t) for k, ws in CATEGORIES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "Trao đổi chung"


def _short(name):
    return re.sub(r"\s*\(@.*\)", "", name or "").strip() or "Không rõ"


def _rule_analyze(msgs):
    groups, tasks, issues, proposals, seen = {}, [], [], [], set()
    for m in msgs:
        raw = (m["text"] or "").strip()
        t = raw.lower()
        if not raw or QUESTION_ONLY.match(raw):
            continue
        who = _short(m["user_name"])
        groups.setdefault(_category(t), []).append(f"{who}: {raw[:160]}")
        key = t[:80]
        if key in seen:
            continue
        seen.add(key)
        if ISSUE_RE.search(t):
            issues.append({"van_de": raw[:300], "nguoi_lien_quan": who,
                           "muc_do": "Cao" if HIGH_RE.search(t) else "Trung bình", "de_xuat": UNKNOWN})
        if PROPOSAL_RE.search(t):
            proposals.append({"noi_dung": raw[:300], "nguoi": who})
        if TASK_RE.search(t) or MENTION_RE.search(raw) or PCT_RE.search(t):
            mention = MENTION_RE.search(raw)
            dl = DEADLINE_RE.search(t)
            pct = PCT_RE.search(t)
            status = "Hoàn thành" if DONE_RE.search(t) and not re.search(r"\bcần\b|\bphải\b", t) else (
                "Đang làm" if DOING_RE.search(t) else "Chưa làm")
            tasks.append({"cong_viec": raw[:300], "nguoi_phu_trach": mention[1] if mention else who,
                          "thoi_han": dl[0] if dl else UNKNOWN, "trang_thai": status,
                          "tien_do": f"{pct[1]}%" if pct else ("100%" if status == "Hoàn thành" else "")})
    users = sorted({_short(m["user_name"]) for m in msgs})
    chats = sorted({m["chat_title"] or str(m["chat_id"]) for m in msgs})
    top = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    done = sum(1 for x in tasks if x["trang_thai"] == "Hoàn thành")
    parts = [f"Có {len(msgs)} tin nhắn từ {len(users)} người trong {len(chats)} nhóm ({', '.join(chats)})."]
    if top:
        parts.append("Trao đổi nhiều nhất về: " + ", ".join(f"{k} ({len(v)} tin)" for k, v in top[:3]) + ".")
    high = sum(1 for i in issues if i["muc_do"] == "Cao")
    parts.append(f"Ghi nhận {len(tasks)} đầu việc ({done} đã hoàn thành), {len(issues)} vấn đề cần xử lý"
                 + (f", trong đó {high} vấn đề mức CAO cần sếp chú ý." if high else "."))
    return {"tom_tat": " ".join(parts),
            "theo_nhom": [{"ten": k, "noi_dung": v[:8]} for k, v in top],
            "dau_viec": tasks, "van_de": issues, "de_xuat": proposals, "thong_tin_quan_trong": [], "ai": False}


# ======================= Phân tích bằng ChatGPT =======================
PROMPT = """Bạn là EIRAA AI – trợ lý tổng hợp cho giám đốc. Dưới đây là tin nhắn trao đổi trong các nhóm Telegram công ty
(định dạng: [nhóm] [giờ] người gửi: nội dung). Viết tiếng Việt, ngắn gọn.
QUY TẮC: chỉ dùng thông tin có trong tin nhắn, KHÔNG suy diễn; thiếu thông tin ghi "Chưa xác định".
Trả về JSON:
{"tom_tat": "5-8 câu: nội dung chính, kết quả, điểm cần sếp chú ý",
 "theo_nhom": [{"ten": "nhóm công việc (Kinh doanh, Kỹ thuật, Tài chính, Nhân sự, Marketing, ...)", "noi_dung": ["ý chính"]}],
 "dau_viec": [{"cong_viec": "...", "nguoi_phu_trach": "...", "thoi_han": "...", "trang_thai": "Chưa làm|Đang làm|Hoàn thành", "tien_do": "vd 60%"}],
 "van_de": [{"van_de": "...", "nguoi_lien_quan": "...", "muc_do": "Cao|Trung bình|Thấp", "de_xuat": "..."}],
 "de_xuat": [{"noi_dung": "...", "nguoi": "..."}],
 "thong_tin_quan_trong": ["thông tin quan trọng khác cần đưa vào báo cáo"]}"""


def _ai_analyze(msgs):
    c = ai.client()
    if not c:
        return None
    text = "\n".join(f"[{m['chat_title']}] [{m['sent_at'][5:10]} {m['sent_at'][11:16]}] {_short(m['user_name'])}: {m['text']}"
                     for m in msgs)
    try:
        r = c.chat.completions.create(model=ai.model(), temperature=0.2, response_format={"type": "json_object"},
                                      messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": text[-100_000:]}])
        d = json.loads(r.choices[0].message.content)
        for k in ("theo_nhom", "dau_viec", "van_de", "de_xuat", "thong_tin_quan_trong"):
            d[k] = d.get(k) or []
        d["tom_tat"] = d.get("tom_tat") or ""
        d["ai"] = True
        return d
    except Exception as e:
        log.warning("OpenAI lỗi, dùng chế độ cơ bản: %s", e)
        return None


# ======================= Dựng báo cáo =======================
def build(period_text="hôm nay", chat_ids=None):
    start, end, label = summary.parse_period(period_text)
    chat_ids = chat_ids if chat_ids is not None else (find_chats(period_text) or None)
    msgs = get_messages(start, end, chat_ids)
    a = (_ai_analyze(msgs) if msgs else None) or _rule_analyze(msgs)
    chats = sorted({m["chat_title"] or str(m["chat_id"]) for m in msgs})
    users = sorted({_short(m["user_name"]) for m in msgs})
    rows = [{"hang_muc": "Công việc", "noi_dung": t.get("cong_viec", ""), "nguoi": t.get("nguoi_phu_trach") or UNKNOWN,
             "thoi_han": t.get("thoi_han") or UNKNOWN,
             "trang_thai": (t.get("trang_thai") or UNKNOWN) + (f" ({t['tien_do']})" if t.get("tien_do") else "")} for t in a["dau_viec"]]
    rows += [{"hang_muc": "Vấn đề", "noi_dung": v.get("van_de", ""), "nguoi": v.get("nguoi_lien_quan") or UNKNOWN,
              "thoi_han": UNKNOWN, "trang_thai": f"Cần xử lý – mức {v.get('muc_do') or UNKNOWN}"} for v in a["van_de"]]
    rows += [{"hang_muc": "Đề xuất", "noi_dung": p.get("noi_dung", ""), "nguoi": p.get("nguoi") or UNKNOWN,
              "thoi_han": UNKNOWN, "trang_thai": "Chờ xác nhận"} for p in a["de_xuat"]]
    done = sum(1 for t in a["dau_viec"] if t.get("trang_thai") == "Hoàn thành")
    high = sum(1 for v in a["van_de"] if v.get("muc_do") == "Cao")
    scope = (" – " + ", ".join(chats).upper()) if chat_ids and chats else ""
    return {
        "kind": "tin_nhan", "title": f"TỔNG HỢP TIN NHẮN TELEGRAM {label.upper()}{scope}", "label": label,
        "generated_at": db.now().strftime("%H:%M %d/%m/%Y"), "source": "tin nhắn nhóm Telegram",
        "stat_lines": [("Số tin nhắn", len(msgs)), ("Số nhóm", len(chats)), ("Người tham gia", len(users)),
                       ("Đầu việc", len(a["dau_viec"])), ("Đã hoàn thành", done), ("Vấn đề cần xử lý", len(a["van_de"])),
                       ("Vấn đề mức cao", high)],
        "group_label": "Theo nhóm công việc",
        "theo_phong_ban": [{"ten": g["ten"], "tom_tat": "; ".join(g.get("noi_dung", [])[:6])} for g in a["theo_nhom"]],
        "rui_ro": [f"[{v.get('muc_do') or UNKNOWN}] {v.get('nguoi_lien_quan') or ''}: {v.get('van_de', '')}" for v in a["van_de"]][:10],
        "goi_y": a.get("thong_tin_quan_trong", [])[:5],
        "tom_tat": a["tom_tat"], "ai": a.get("ai", False), "rows": rows,
        "analysis": a, "messages": msgs, "chats": chats,
        "stats": {}, "missing": [], "overdue": [], "open_tasks": [], "reports": [],
    }


def to_message(rep, detail=True):
    a = rep["analysis"]
    s = dict(rep["stat_lines"])
    if not rep["messages"]:
        return (f"💬 {rep['title']}\n\nChưa có tin nhắn nào trong {rep['label']}.\n"
                "Hãy thêm bot vào nhóm Telegram (và tắt Privacy Mode trong @BotFather) để bot ghi nhận tin nhắn.")
    L = [f"💬 {rep['title']}", f"🕒 Lập lúc {rep['generated_at']}" + ("" if rep.get("ai") else " · chế độ cơ bản (chưa bật AI)"), "",
         f"📨 {s['Số tin nhắn']} tin nhắn · 👥 {s['Người tham gia']} người · 💬 {s['Số nhóm']} nhóm: {', '.join(rep['chats'])}",
         "", "📝 TÓM TẮT", rep["tom_tat"]]
    if detail and a["theo_nhom"]:
        L += ["", "📂 PHÂN LOẠI THEO NHÓM CÔNG VIỆC"]
        for g in a["theo_nhom"][:6]:
            L.append(f"▸ {g['ten']} ({len(g.get('noi_dung', []))})")
            L += [f"   • {x[:140]}" for x in g.get("noi_dung", [])[:3]]
    if a["dau_viec"]:
        icon = {"Hoàn thành": "✅", "Đang làm": "⏳"}
        L += ["", f"📌 ĐẦU VIỆC & TIẾN ĐỘ ({len(a['dau_viec'])})"] + [
            f"{icon.get(t.get('trang_thai'), '🔲')} {t.get('cong_viec', '')[:150]} — {t.get('nguoi_phu_trach') or UNKNOWN}"
            + (f" | hạn: {t['thoi_han']}" if t.get("thoi_han") and t["thoi_han"] != UNKNOWN else "")
            + (f" | {t['tien_do']}" if t.get("tien_do") else "") for t in a["dau_viec"][:12]]
    if a["van_de"]:
        L += ["", f"⚠️ VẤN ĐỀ CẦN XỬ LÝ ({len(a['van_de'])})"] + [
            f"• [{v.get('muc_do') or UNKNOWN}] {v.get('nguoi_lien_quan') or ''}: {v.get('van_de', '')[:160]}" for v in a["van_de"][:8]]
    if a["de_xuat"]:
        L += ["", "💡 ĐỀ XUẤT"] + [f"• {p.get('nguoi') or ''}: {p.get('noi_dung', '')[:160]}" for p in a["de_xuat"][:5]]
    return "\n".join(L)
