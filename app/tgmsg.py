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


def save_message(chat_id, message_id, user_id, user_name, text, sent_at: datetime, kind="text", file_name=None, source="group"):
    """source: group (tin nhắn nhóm) | private (chat riêng của tài khoản sếp) | forward (sếp chuyển tiếp cho bot)."""
    with db.conn() as c:
        c.execute("""INSERT OR IGNORE INTO tg_messages(chat_id,message_id,user_id,user_name,kind,text,file_name,sent_at,source)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (chat_id, message_id, user_id, user_name, kind, text, file_name, sent_at.astimezone(VN_TZ).isoformat(), source))


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


# ======================= Tin nhắn trên WEB (nội bộ + khách hàng) =======================
INTERNAL_ID = -1                     # kênh chat nội bộ giữa đồng nghiệp


def ensure_internal():
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM tg_chats WHERE chat_id=?", (INTERNAL_ID,)).fetchone():
            c.execute("INSERT INTO tg_chats(chat_id,title,type,active,added_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (INTERNAL_ID, "Chat nội bộ", "web_internal", 1, db.now().isoformat(), db.now().isoformat()))


def customer_thread(name, company="", phone=""):
    """Tìm hoặc tạo hội thoại khách hàng (theo số điện thoại, nếu không có thì theo tên)."""
    import zlib
    key = re.sub(r"\D", "", phone or "") or (name or "").strip().lower()
    cid = -(10_000_000 + zlib.crc32(key.encode()) % 89_999_999)
    title = (name or "Khách hàng").strip() + (f" – {company.strip()}" if company and company.strip() else "")
    with db.conn() as c:
        row = c.execute("SELECT title FROM tg_chats WHERE chat_id=?", (cid,)).fetchone()
        if not row:
            c.execute("INSERT INTO tg_chats(chat_id,title,type,active,added_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (cid, title + (f" ({phone.strip()})" if phone and phone.strip() else ""), "web_customer", 1,
                       db.now().isoformat(), db.now().isoformat()))
    return cid


def post_web(chat_id, sender, text, user_id=None, sent_at=None):
    """Lưu 1 tin nhắn web. Người gửi là nhân viên (user_id) hoặc khách hàng (user_id=None)."""
    with db.conn() as c:
        ch = c.execute("SELECT type FROM tg_chats WHERE chat_id=?", (chat_id,)).fetchone()
        if not ch or ch["type"] not in ("web_internal", "web_customer"):
            raise ValueError("Không tìm thấy cuộc trò chuyện")
        mid = c.execute("SELECT COALESCE(MAX(message_id),0)+1 FROM tg_messages WHERE chat_id=?", (chat_id,)).fetchone()[0]
        c.execute("UPDATE tg_chats SET updated_at=? WHERE chat_id=?", (db.now().isoformat(), chat_id))
        source = ch["type"]
    save_message(chat_id, mid, user_id, sender, text.strip(), sent_at or db.now(), source=source)


def list_threads():
    ensure_internal()
    with db.conn() as c:
        return db.rows(c, """SELECT ch.chat_id, ch.title, ch.type, COUNT(m.id) AS total, MAX(m.sent_at) AS last_at,
                                    (SELECT text FROM tg_messages x WHERE x.chat_id=ch.chat_id ORDER BY x.sent_at DESC LIMIT 1) AS last_text,
                                    (SELECT user_id IS NULL FROM tg_messages x WHERE x.chat_id=ch.chat_id ORDER BY x.sent_at DESC LIMIT 1) AS waiting
                             FROM tg_chats ch LEFT JOIN tg_messages m ON m.chat_id=ch.chat_id
                             WHERE ch.type IN ('web_internal','web_customer')
                             GROUP BY ch.chat_id ORDER BY ch.type DESC, last_at DESC""")


def thread_messages(chat_id, limit=300):
    with db.conn() as c:
        rows = db.rows(c, "SELECT * FROM tg_messages WHERE chat_id=? ORDER BY sent_at DESC, id DESC LIMIT ?", (chat_id, limit))
    return rows[::-1]


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
PROGRESS_CTX = re.compile(r"\b(đang|xong|hoàn thành|tiến độ|khoảng|được|đạt|làm)\b", re.I)
NOT_PROGRESS = re.compile(r"\b(giảm|chiết khấu|tăng|lãi|khuyến mãi|thuế|vat)\b", re.I)
DIRECTIVE_RE = re.compile(r"\b(cần|phải|nhờ|giao|chuẩn bị|nhắc)\b", re.I)
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
        resolved = bool(DONE_RE.search(t)) and not DIRECTIVE_RE.search(t)
        if ISSUE_RE.search(t) and not resolved and not (DIRECTIVE_RE.search(t) and not HIGH_RE.search(t)):
            issues.append({"van_de": raw[:300], "nguoi_lien_quan": who,
                           "muc_do": "Cao" if HIGH_RE.search(t) else "Trung bình", "de_xuat": UNKNOWN})
        if PROPOSAL_RE.search(t):
            proposals.append({"noi_dung": raw[:300], "nguoi": who})
        is_issue_only = ISSUE_RE.search(t) and not DIRECTIVE_RE.search(t) and not resolved
        is_proposal = PROPOSAL_RE.search(t) and not DIRECTIVE_RE.search(t)
        pct = PCT_RE.search(t) if (PROGRESS_CTX.search(t) and not NOT_PROGRESS.search(t)) else None
        if (TASK_RE.search(t) or MENTION_RE.search(raw) or pct) and not is_issue_only and not is_proposal:
            mention = MENTION_RE.search(raw)
            dl = DEADLINE_RE.search(t)
            planned = re.search(r"\b(dự kiến|sẽ|cần|phải)\b", t)
            status = "Hoàn thành" if DONE_RE.search(t) and not planned else (
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


# ======================= Nội dung báo cáo & tiến độ =======================
def _cell(v):
    v = v.strip()
    if re.fullmatch(r"\d{4,}(\.0)?", v):                 # 80000000 -> 80.000.000
        return f"{int(float(v)):,}".replace(",", ".")
    return v


def _excerpt(text, limit=220):
    """Lấy phần nội dung chính trong file báo cáo, dễ đọc (bảng Excel -> 'ô – ô – ô')."""
    out, header_skipped = [], False
    for raw in (text or "").splitlines():
        x = re.sub(r"\s+", " ", raw).strip()
        if not x or x.startswith("[Sheet"):
            continue
        if " | " in x:                                         # dòng của bảng
            if not header_skipped:                             # bỏ dòng tiêu đề cột
                header_skipped = True
                continue
            cells = [_cell(c) for c in x.split("|") if c.strip()]
            x = " – ".join(cells)
        out.append(x)
    if len(out) > 1:
        out = out[1:]                                          # bỏ dòng tiêu đề file
    text = " · ".join(out)
    return (text[:limit].rsplit(" ", 1)[0] + "…") if len(text) > limit else text


def _reports_part(start, end, department=None):
    data = summary.collect(start, end, department)
    reports = []
    for r in data["reports"]:
        atts = r.get("attachments", [])
        subjects = [a.get("subject") or a["filename"] for a in atts] or [x for x in r["done"].splitlines() if x.strip()]
        content = " | ".join(_excerpt(a.get("content")) for a in atts if a.get("content")) or r["done"]
        reports.append({"nguoi": r["full_name"], "phong": r["department"] or "", "ngay": r["report_date"],
                        "chu_de": "; ".join(dict.fromkeys(subjects)), "noi_dung": content[:500],
                        "tep": ", ".join(a["filename"] for a in atts), "tien_do": r["progress"],
                        "van_de": r["issues"], "muc_do": r["issue_level"]})
    tasks = data["tasks"]
    today = db.today().isoformat()
    for t in tasks:
        t["qua_han"] = t["status"] != "Hoàn thành" and bool(t["deadline"]) and t["deadline"] < today
    return {"reports": reports, "missing": data["missing"], "staff": data["staff"], "tasks": tasks}


# ======================= Phân tích bằng ChatGPT =======================
PROMPT = """Bạn là EIRAA AI – trợ lý tổng hợp cho giám đốc. Dữ liệu gồm: (1) tin nhắn KHÁCH HÀNG gửi tới công ty và nhân viên trả lời,
(2) tin nhắn trao đổi giữa ĐỒNG NGHIỆP (chat nội bộ, nhóm), (3) tiến độ công việc, (4) nội dung các bài báo cáo nhân viên nộp.
Viết tiếng Việt, ngắn gọn. QUY TẮC: chỉ dùng thông tin có trong dữ liệu, KHÔNG suy diễn; thiếu thông tin ghi "Chưa xác định".
Trả về JSON:
{"tom_tat": "6-10 câu tổng hợp chung: khách hàng cần gì, đồng nghiệp đang làm gì, tiến độ, việc chậm, điểm sếp cần chú ý",
 "khach_hang": [{"ten": "tên khách / hội thoại", "tom_tat": "khách cần gì, đã trả lời chưa, bước tiếp theo"}],
 "theo_nhom": [{"ten": "nhóm công việc (Kinh doanh, Kỹ thuật, Tài chính, Nhân sự, Marketing, ...)", "noi_dung": ["ý chính tin nhắn đồng nghiệp"]}],
 "dau_viec": [{"cong_viec": "...", "nguoi_phu_trach": "...", "thoi_han": "...", "trang_thai": "Chưa làm|Đang làm|Hoàn thành", "tien_do": "vd 60%"}],
 "van_de": [{"van_de": "...", "nguoi_lien_quan": "...", "muc_do": "Cao|Trung bình|Thấp", "de_xuat": "..."}],
 "de_xuat": [{"noi_dung": "...", "nguoi": "..."}],
 "bao_cao": [{"nguoi": "tên người nộp", "noi_dung_chinh": "1-2 câu tóm tắt bài báo cáo"}],
 "thong_tin_quan_trong": ["..."]}"""


def _src_label(m):
    return {"web_customer": f"Khách hàng – {m.get('chat_title')}", "web_internal": "Nội bộ"}.get(m.get("source"),
                                                                                                f"Nhóm {m.get('chat_title')}")


def _ai_analyze(msgs, part):
    c = ai.client()
    if not c:
        return None
    text = ["(1)(2) TIN NHẮN:"] + [f"[{_src_label(m)}] [{m['sent_at'][5:10]} {m['sent_at'][11:16]}] "
                                    f"{_short(m['user_name'])}{'' if m.get('user_id') or m.get('source') != 'web_customer' else ' (khách)'}: {m['text']}"
                                    for m in msgs]
    text += ["", "(3) TIẾN ĐỘ CÔNG VIỆC:"] + [f"- {t['title']} – {t['assignee']} – hạn {t['deadline'] or 'chưa có'} – {t['status']} {t['progress']}%"
                                             + (" – QUÁ HẠN" if t["qua_han"] else "") for t in part["tasks"]]
    text += ["", "(4) BÁO CÁO ĐÃ NỘP:"] + [f"- {r['nguoi']} ({r['phong']}) [{r['ngay']}] chủ đề: {r['chu_de']}\n  Nội dung: {r['noi_dung']}"
                                          for r in part["reports"]]
    text += ["Chưa nộp: " + (", ".join(m["full_name"] for m in part["missing"]) or "không")]
    try:
        r = c.chat.completions.create(model=ai.model(), temperature=0.2, response_format={"type": "json_object"},
                                      messages=[{"role": "system", "content": PROMPT},
                                                {"role": "user", "content": "\n".join(text)[-100_000:]}])
        d = json.loads(r.choices[0].message.content)
        for k in ("khach_hang", "theo_nhom", "dau_viec", "van_de", "de_xuat", "bao_cao", "thong_tin_quan_trong"):
            d[k] = d.get(k) or []
        d["tom_tat"] = d.get("tom_tat") or ""
        d["ai"] = True
        return d
    except Exception as e:
        log.warning("OpenAI lỗi, dùng chế độ cơ bản: %s", e)
        return None


# ======================= Hội thoại khách hàng (không cần AI) =======================
KEY_RE = re.compile(r"\b(giá|báo giá|đơn|đặt|hợp đồng|thanh toán|giao hàng|lỗi|không|gấp|khi nào|bao giờ|bảo hành|đổi|trả|hủy|chiết khấu|giảm)\b", re.I)


def _customer_threads(msgs):
    threads = {}
    for m in msgs:
        if m.get("source") == "web_customer":
            threads.setdefault(m["chat_id"], []).append(m)
    out = []
    for cid, ms in threads.items():
        from_customer = [m for m in ms if m.get("user_id") is None]
        key = [m for m in from_customer if KEY_RE.search(m["text"] or "")] or from_customer
        last = ms[-1]
        waiting = last.get("user_id") is None
        staff = sorted({_short(m["user_name"]) for m in ms if m.get("user_id") is not None})
        out.append({"chat_id": cid, "ten": ms[0].get("chat_title") or "Khách hàng", "so_tin": len(ms),
                    "lan_cuoi": last["sent_at"], "cho_phan_hoi": waiting, "nhan_vien": staff,
                    "noi_dung": [m["text"][:160] for m in key[-3:]],
                    "tra_loi_cuoi": next((m["text"][:160] for m in reversed(ms) if m.get("user_id") is not None), "")})
    return sorted(out, key=lambda x: (not x["cho_phan_hoi"], x["lan_cuoi"]), reverse=False)


# ======================= Dựng bản tổng hợp =======================
def build(period_text="hôm nay", chat_ids=None):
    start, end, label = summary.parse_period(period_text)
    chat_ids = chat_ids if chat_ids is not None else (find_chats(period_text) or None)
    department = summary.find_department(period_text)
    msgs = get_messages(start, end, chat_ids)
    cus_msgs = [m for m in msgs if m.get("source") == "web_customer"]
    col_msgs = [m for m in msgs if m.get("source") != "web_customer"]
    part = _reports_part(start, end, department)
    a = (_ai_analyze(msgs, part) if (msgs or part["reports"]) else None)
    if not a:
        a = _rule_analyze(col_msgs)
        cus_issues = _rule_analyze([m for m in cus_msgs if m.get("user_id") is None])["van_de"]   # chỉ tin khách gửi
        for v in cus_issues:
            v["nguoi_lien_quan"] = "Khách " + (v.get("nguoi_lien_quan") or "")
        a["van_de"] = cus_issues + a["van_de"]
        a["khach_hang"] = []
    threads = _customer_threads(msgs)
    ai_cus = {x.get("ten"): x.get("tom_tat") for x in a.get("khach_hang", [])}
    for t in threads:
        t["tom_tat"] = ai_cus.get(t["ten"]) or " · ".join(t["noi_dung"])
    chats = sorted({m["chat_title"] or str(m["chat_id"]) for m in col_msgs if m.get("source") != "web_internal"})
    tasks = part["tasks"]
    t_done = [t for t in tasks if t["status"] == "Hoàn thành"]
    t_doing = [t for t in tasks if t["status"] == "Đang làm" and not t["qua_han"]]
    t_todo = [t for t in tasks if t["status"] not in ("Hoàn thành", "Đang làm") and not t["qua_han"]]
    t_late = [t for t in tasks if t["qua_han"]]
    reps, missing, staff = part["reports"], part["missing"], part["staff"]
    ai_report = {x.get("nguoi"): x.get("noi_dung_chinh") for x in a.get("bao_cao", [])}
    for r in reps:
        r["tom_tat"] = ai_report.get(r["nguoi"]) or r["noi_dung"]
    waiting = [t for t in threads if t["cho_phan_hoi"]]

    if not a.get("ai"):   # tóm tắt chung khi chưa có AI
        parts = []
        parts.append(f"Khách hàng: {len(threads)} cuộc trò chuyện ({len(cus_msgs)} tin), "
                     + (f"{len(waiting)} khách đang chờ phản hồi." if waiting else "tất cả đã được phản hồi."))
        if col_msgs:
            parts.append(a["tom_tat"].replace("Có ", "Đồng nghiệp: ", 1))
        else:
            parts.append("Đồng nghiệp: chưa có tin nhắn trao đổi.")
        parts.append(f"Báo cáo: {len({r['nguoi'] for r in reps})}/{len(staff)} nhân viên đã nộp.")
        parts.append(f"Công việc: {len(t_done)} hoàn thành, {len(t_doing)} đang làm, {len(t_todo)} chưa làm, {len(t_late)} quá hạn.")
        a["tom_tat"] = " ".join(parts)

    rows = [{"hang_muc": "Khách hàng", "noi_dung": f"{t['ten']}: {t['tom_tat']}", "nguoi": ", ".join(t["nhan_vien"]) or UNKNOWN,
             "thoi_han": t["lan_cuoi"][11:16] + " " + summary._fmt_date(t["lan_cuoi"][:10]),
             "trang_thai": "Chờ phản hồi" if t["cho_phan_hoi"] else "Đã phản hồi"} for t in threads]
    rows += [{"hang_muc": "Tin nhắn – việc", "noi_dung": t.get("cong_viec", ""), "nguoi": t.get("nguoi_phu_trach") or UNKNOWN,
              "thoi_han": t.get("thoi_han") or UNKNOWN,
              "trang_thai": (t.get("trang_thai") or UNKNOWN) + (f" ({t['tien_do']})" if t.get("tien_do") else "")} for t in a["dau_viec"]]
    rows += [{"hang_muc": "Vấn đề", "noi_dung": v.get("van_de", ""), "nguoi": v.get("nguoi_lien_quan") or UNKNOWN,
              "thoi_han": UNKNOWN, "trang_thai": f"Cần xử lý – mức {v.get('muc_do') or UNKNOWN}"} for v in a["van_de"]]
    rows += [{"hang_muc": "Đề xuất", "noi_dung": p.get("noi_dung", ""), "nguoi": p.get("nguoi") or UNKNOWN,
              "thoi_han": UNKNOWN, "trang_thai": "Chờ xác nhận"} for p in a["de_xuat"]]
    rows += [{"hang_muc": "Tiến độ", "noi_dung": t["title"], "nguoi": t["assignee"] or UNKNOWN,
              "thoi_han": summary._fmt_date(t["deadline"]),
              "trang_thai": f"{'QUÁ HẠN – ' if t['qua_han'] else ''}{t['status']} ({t['progress']}%)"} for t in tasks]
    rows += [{"hang_muc": "Báo cáo", "noi_dung": f"{r['chu_de']}: {r['tom_tat']}", "nguoi": r["nguoi"],
              "thoi_han": summary._fmt_date(r["ngay"]), "trang_thai": "Đã nộp"} for r in reps]
    rows += [{"hang_muc": "Chưa nộp", "noi_dung": f"Chưa nộp báo cáo ({m['ngay_thieu']})", "nguoi": m["full_name"],
              "thoi_han": UNKNOWN, "trang_thai": "Chưa nộp"} for m in missing]

    scope = (f" – {department.upper()}" if department else "")
    return {
        "kind": "tin_nhan", "title": f"TỔNG HỢP TIN NHẮN & TIẾN ĐỘ CÔNG VIỆC {label.upper()}{scope}", "label": label,
        "generated_at": db.now().strftime("%H:%M %d/%m/%Y"),
        "source": "tin nhắn khách hàng & đồng nghiệp trên web + tiến độ công việc + báo cáo nhân viên",
        "stat_lines": [("Tin nhắn khách hàng", len(cus_msgs)), ("Khách chờ phản hồi", len(waiting)),
                       ("Tin nhắn đồng nghiệp", len(col_msgs)),
                       ("Đã nộp báo cáo", f"{len({r['nguoi'] for r in reps})}/{len(staff)}"),
                       ("Việc hoàn thành", len(t_done)), ("Việc đang làm", len(t_doing)), ("Việc quá hạn", len(t_late)),
                       ("Vấn đề cần xử lý", len(a["van_de"]))],
        "group_label": "Tin nhắn đồng nghiệp theo nhóm công việc",
        "theo_phong_ban": [{"ten": g["ten"], "tom_tat": "; ".join(g.get("noi_dung", [])[:6])} for g in a["theo_nhom"]],
        "rui_ro": [f"Khách chờ phản hồi: {t['ten']}" for t in waiting]
                  + [f"[{v.get('muc_do') or UNKNOWN}] {v.get('nguoi_lien_quan') or ''}: {v.get('van_de', '')}" for v in a["van_de"]][:10]
                  + [f"Quá hạn: {t['title']} ({t['assignee']}, hạn {summary._fmt_date(t['deadline'])})" for t in t_late],
        "goi_y": a.get("thong_tin_quan_trong", [])[:5],
        "tom_tat": a["tom_tat"], "ai": a.get("ai", False), "rows": rows,
        "analysis": a, "messages": msgs, "chats": chats, "threads": threads, "col_count": len(col_msgs),
        "task_groups": {"done": t_done, "doing": t_doing, "todo": t_todo, "late": t_late},
        "report_items": reps, "missing_staff": missing, "staff_count": len(staff),
        "extra_sheets": [
            ("Khách hàng", ["Khách hàng", "Số tin", "Lần cuối", "Trạng thái", "Nhân viên phụ trách", "Nội dung chính", "Trả lời gần nhất"],
             [[t["ten"], t["so_tin"], t["lan_cuoi"][:16].replace("T", " "), "Chờ phản hồi" if t["cho_phan_hoi"] else "Đã phản hồi",
               ", ".join(t["nhan_vien"]), t["tom_tat"], t["tra_loi_cuoi"]] for t in threads], [26, 8, 17, 14, 22, 70, 50]),
            ("Tiến độ công việc", ["Công việc", "Phụ trách", "Phòng ban", "Hạn", "Trạng thái", "Tiến độ %", "Quá hạn"],
             [[t["title"], t["assignee"], t["department"], summary._fmt_date(t["deadline"]), t["status"], t["progress"],
               "Có" if t["qua_han"] else ""] for t in tasks], [44, 22, 16, 12, 14, 10, 9]),
            ("Nội dung báo cáo", ["Ngày", "Người nộp", "Phòng ban", "Chủ đề", "Nội dung chính", "Tệp"],
             [[summary._fmt_date(r["ngay"]), r["nguoi"], r["phong"], r["chu_de"], r["tom_tat"], r["tep"]] for r in reps],
             [12, 20, 14, 32, 80, 28]),
            ("Đồng nghiệp – theo nhóm", ["Nhóm công việc", "Nội dung tin nhắn"],
             [[g["ten"], x] for g in a["theo_nhom"] for x in g.get("noi_dung", [])], [26, 100]),
            ("Tin nhắn gốc", ["Thời gian", "Loại", "Cuộc trò chuyện", "Người gửi", "Nội dung"],
             [[m["sent_at"][:16].replace("T", " "), {"web_customer": "Khách hàng", "web_internal": "Đồng nghiệp"}.get(m.get("source"), "Nhóm Telegram"),
               m.get("chat_title") or "", m["user_name"], m["text"]] for m in msgs], [17, 14, 26, 22, 90]),
        ],
        "stats": {}, "missing": [], "overdue": [], "open_tasks": [], "reports": [],
    }


def _hm(iso):
    return f"{iso[11:16]} {iso[8:10]}/{iso[5:7]}"


def _cut(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _split_title(title):
    """'Anh Minh – Công ty Minh Phát (0901234567)' -> ('Anh Minh', 'Công ty Minh Phát')."""
    t = re.sub(r"\s*\([\d +.]+\)\s*$", "", title or "")
    name, _, company = t.partition(" – ")
    return name.strip() or "Khách hàng", company.strip()


def _first_name(full):
    """Tên gọi ngắn: 'Lê Minh Hùng' -> 'Hùng'; tên dễ nhầm như 'Anh' -> 'Đức Anh'."""
    parts = re.sub(r"\s*\(.*?\)", "", full or "").split()
    if not parts:
        return ""
    if parts[-1].lower() in ("anh", "em", "chị") and len(parts) > 1:
        return " ".join(parts[-2:])
    return parts[-1]


def to_message(rep, detail=True):
    """Bản tổng hợp gửi Telegram (định dạng HTML)."""
    from html import escape as e
    a, tg = rep["analysis"], rep["task_groups"]
    reps, threads = rep["report_items"], rep["threads"]
    waiting = [t for t in threads if t["cho_phan_hoi"]]
    col_issues = [v for v in a["van_de"] if not str(v.get("nguoi_lien_quan") or "").startswith("Khách ")]
    urgent = [v for v in col_issues if v.get("muc_do") == "Cao"]
    watch = [v for v in col_issues if v.get("muc_do") != "Cao"]
    n_rep = len({r["nguoi"] for r in reps})
    SEP = "━━━━━━━━━━━━━━━━"

    L = [f"📊 <b>TỔNG HỢP TIN NHẮN</b>",
         f"📅 {e(rep['label'][:1].upper() + rep['label'][1:])} · 🕒 {rep['generated_at'][:5]}"
         + ("" if rep.get("ai") else " · <i>chế độ cơ bản</i>"),
         "",
         "📌 <b>TÓM TẮT NHANH</b>",
         f"👥 Khách hàng: <b>{len(threads)}</b> khách" + (f" · ⏳ <b>{len(waiting)} chờ phản hồi</b>" if waiting else " · ✅ đã trả lời hết"),
         f"💬 Đồng nghiệp: <b>{rep['col_count']}</b> tin nhắn" + (f" · 🔴 <b>{len(urgent)} việc gấp</b>" if urgent else ""),
         f"📋 Công việc: ⏰ <b>{len(tg['late'])}</b> quá hạn · ⏳ {len(tg['doing'])} đang làm · ✅ {len(tg['done'])} xong",
         f"📄 Báo cáo: <b>{n_rep}/{rep['staff_count']}</b> người đã nộp"]
    if rep.get("ai") and rep["tom_tat"]:
        L += ["", f"📝 {e(rep['tom_tat'])}"]

    # ---- 1. Khách hàng ----
    L += ["", SEP, f"1️⃣ <b>KHÁCH HÀNG</b>  ({len(threads)} khách · ⏳ {len(waiting)} chờ phản hồi)", ""]
    if not threads:
        L.append("<i>Không có tin nhắn khách hàng.</i>")
    for t in threads[:8]:
        name, company = _split_title(t["ten"])
        L.append(f"{'⏳' if t['cho_phan_hoi'] else '✅'} <b>{e(name)}</b>" + (f" · {e(company)}" if company else ""))
        status = "<i>chưa ai trả lời</i>" if t["cho_phan_hoi"] and not t["nhan_vien"] else (
            "<i>khách đang chờ trả lời</i>" if t["cho_phan_hoi"] else f"{e(', '.join(_first_name(x) for x in t['nhan_vien']))} đã trả lời")
        L.append(f"     🕒 {_hm(t['lan_cuoi'])} · {t['so_tin']} tin · {status}")
        if rep.get("ai") and t.get("tom_tat"):
            L.append(f"     📝 {e(_cut(t['tom_tat'], 160))}")
        else:
            for q in t["noi_dung"][-2:]:
                L.append(f"     💬 “{e(_cut(q, 110))}”")
            if not t["cho_phan_hoi"] and t["tra_loi_cuoi"]:
                L.append(f"     ↪️ <i>{e(_cut(t['tra_loi_cuoi'], 100))}</i>")
        L.append("")
    if len(threads) > 8:
        L.append(f"<i>… và {len(threads) - 8} khách khác (xem file Excel)</i>")

    # ---- 2. Đồng nghiệp ----
    L += [SEP, f"2️⃣ <b>ĐỒNG NGHIỆP</b>  ({rep['col_count']} tin nhắn)", ""]
    if not rep["col_count"]:
        L.append("<i>Chưa có tin nhắn trao đổi.</i>")
    if urgent:
        L.append("🔴 <b>Cần xử lý gấp</b>")
        L += [f"     • <b>{e(_first_name(v.get('nguoi_lien_quan')))}</b>: {e(_cut(v.get('van_de'), 110))}" for v in urgent[:4]]
        L.append("")
    if watch:
        L.append("🟡 <b>Cần theo dõi</b>")
        L += [f"     • <b>{e(_first_name(v.get('nguoi_lien_quan')))}</b>: {e(_cut(v.get('van_de'), 110))}" for v in watch[:4]]
        L.append("")
    if a["theo_nhom"]:
        L.append("📂 <b>Nội dung trao đổi</b>")
        for g in a["theo_nhom"][:5]:
            items = g.get("noi_dung", [])
            L.append(f"     ▸ <b>{e(g['ten'])}</b> ({len(items)})")
            for x in items[:2]:
                who, _, msg = x.partition(": ")
                L.append(f"        – {e(_first_name(who))}: {e(_cut(msg or who, 95))}")
        L.append("")
    if a["de_xuat"]:
        L.append("💡 <b>Đề xuất</b>")
        L += [f"     • <b>{e(_first_name(p.get('nguoi')))}</b>: {e(_cut(p.get('noi_dung'), 110))}" for p in a["de_xuat"][:3]]
        L.append("")

    # ---- 3. Tiến độ công việc ----
    L += [SEP, "3️⃣ <b>TIẾN ĐỘ CÔNG VIỆC</b>", ""]

    def task_block(title, items, show_pct=True):
        if not items:
            return
        L.append(title)
        for t in items[:4]:
            L.append(f"     • {e(t['title'])} — <b>{e(_first_name(t['assignee']))}</b>"
                     + (f" · {t['progress']}%" if show_pct else "") + f" · hạn {summary._fmt_date(t['deadline'])[:5]}")
        if len(items) > 4:
            L.append(f"     <i>+ {len(items) - 4} việc khác</i>")
        L.append("")
    task_block(f"⏰ <b>Quá hạn ({len(tg['late'])})</b>", tg["late"])
    task_block(f"⏳ <b>Đang làm ({len(tg['doing'])})</b>", tg["doing"])
    task_block(f"🔲 <b>Chưa làm ({len(tg['todo'])})</b>", tg["todo"], False)
    task_block(f"✅ <b>Đã xong ({len(tg['done'])})</b>", tg["done"], False)
    if not any(tg.values()):
        L += ["<i>Chưa có công việc được giao.</i>", ""]

    # ---- 4. Báo cáo ----
    L += [SEP, f"4️⃣ <b>BÁO CÁO ĐÃ NỘP</b>  ({n_rep}/{rep['staff_count']} người)", ""]
    if not reps:
        L.append("<i>Chưa có báo cáo nào trong kỳ.</i>")
    for r in reps[:10]:
        day = "" if rep["label"].startswith("hôm") else f" [{r['ngay'][8:10]}/{r['ngay'][5:7]}]"
        L.append(f"📄 <b>{e(r['nguoi'])}</b>{day} – {e(r['chu_de'])}")
        L.append(f"     {e(_cut(r['tom_tat'], 150))}")
        L.append("")
    if len(reps) > 10:
        L.append(f"<i>… và {len(reps) - 10} báo cáo khác (xem file Excel)</i>")
    if rep["missing_staff"]:
        L.append("❌ <b>Chưa nộp:</b> " + e(", ".join(m["full_name"] for m in rep["missing_staff"])))
    L += ["", "👇 Bấm nút bên dưới để tải file Excel / Word / PDF"]
    return "\n".join(L)
