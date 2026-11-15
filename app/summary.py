"""Gom dữ liệu báo cáo theo kỳ và dựng nội dung báo cáo tổng hợp cho sếp."""
import re
from datetime import date, timedelta

from . import ai, db

UNKNOWN = "Chưa xác định"


# ---------- Hiểu khoảng thời gian từ câu tiếng Việt ----------
def parse_period(text: str | None):
    """Trả về (start, end, nhãn). Mặc định: hôm nay."""
    t = (text or "").lower().strip()
    d = db.today()
    if m := re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", t):
        dd, mm, yy = int(m[1]), int(m[2]), m[3]
        y = int(yy) + (2000 if yy and len(yy) == 2 else 0) if yy else d.year
        try:
            x = date(y, mm, dd)
            return x, x, f"ngày {x:%d/%m/%Y}"
        except ValueError:
            pass
    if "hôm qua" in t or t in ("yesterday",):
        x = d - timedelta(days=1)
        return x, x, f"ngày {x:%d/%m/%Y} (hôm qua)"
    if "tuần trước" in t:
        s = d - timedelta(days=d.weekday() + 7)
        return s, s + timedelta(days=6), f"tuần trước ({s:%d/%m} – {s + timedelta(days=6):%d/%m/%Y})"
    if "tuần" in t or t == "week":
        s = d - timedelta(days=d.weekday())
        return s, d, f"tuần này ({s:%d/%m} – {d:%d/%m/%Y})"
    if "tháng trước" in t:
        e = d.replace(day=1) - timedelta(days=1)
        return e.replace(day=1), e, f"tháng {e:%m/%Y}"
    if "tháng" in t or t == "month":
        return d.replace(day=1), d, f"tháng {d:%m/%Y}"
    if m := re.search(r"(\d+)\s*ngày", t):
        n = max(1, int(m[1]))
        return d - timedelta(days=n - 1), d, f"{n} ngày gần nhất"
    return d, d, f"hôm nay ({d:%d/%m/%Y})"


def find_department(text):
    t = (text or "").lower()
    for dep in db.departments():
        if dep.lower() in t:
            return dep
    return None


# ---------- Gom dữ liệu ----------
def collect(start: date, end: date, department=None):
    reports = db.list_reports(start, end, department=department)
    staff = [u for u in db.list_users(active_only=True) if u["role"] == "staff"
             and (not department or u["department"] == department)]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    workdays = [x for x in days if x.weekday() < 6] or days          # bỏ Chủ nhật
    submitted = {}
    for r in reports:
        submitted.setdefault(r["user_id"], set()).add(r["report_date"])
    missing = []
    for u in staff:
        lack = [x for x in workdays if x.isoformat() not in submitted.get(u["id"], set())]
        if lack:
            missing.append({**u, "so_ngay_thieu": len(lack),
                            "ngay_thieu": ", ".join(f"{x:%d/%m}" for x in lack[:7]) + ("…" if len(lack) > 7 else "")})
    tasks = db.list_tasks(department=department)
    overdue = [t for t in tasks if t["status"] != "Hoàn thành" and t["deadline"] and t["deadline"] < db.today().isoformat()]
    return {"start": start, "end": end, "department": department, "reports": reports, "staff": staff,
            "missing": missing, "tasks": tasks, "overdue": overdue,
            "updates": db.task_updates_between(start, end), "workdays": len(workdays)}


def _status_from_progress(p):
    p = int(p or 0)
    return "Hoàn thành" if p >= 100 else ("Đang thực hiện" if p > 0 else "Chưa bắt đầu")


def build(start: date, end: date, label: str, department=None, use_ai=True):
    """Dựng toàn bộ nội dung báo cáo (dùng cho tin nhắn Telegram và file xuất)."""
    data = collect(start, end, department)
    multi_day = start != end
    rows = []
    for r in data["reports"]:
        who = f"{r['full_name']}" + (f" ({r['department']})" if r["department"] else "")
        prefix = f"[{date.fromisoformat(r['report_date']):%d/%m}] " if multi_day else ""
        if r["done"].strip():
            rows.append({"hang_muc": "Công việc", "noi_dung": prefix + r["done"].strip(), "nguoi": who,
                         "thoi_han": UNKNOWN, "trang_thai": f"{_status_from_progress(r['progress'])} ({r['progress']}%)"})
        if r["issues"].strip():
            rows.append({"hang_muc": "Vấn đề", "noi_dung": prefix + r["issues"].strip(), "nguoi": who,
                         "thoi_han": UNKNOWN, "trang_thai": f"Cần xử lý – mức {r['issue_level'] or UNKNOWN}"})
        if r["proposals"].strip():
            rows.append({"hang_muc": "Đề xuất", "noi_dung": prefix + r["proposals"].strip(), "nguoi": who,
                         "thoi_han": UNKNOWN, "trang_thai": "Chờ xác nhận"})
    for t in data["overdue"]:
        rows.append({"hang_muc": "Quá hạn", "noi_dung": t["title"], "nguoi": t["assignee"] or UNKNOWN,
                     "thoi_han": _fmt_date(t["deadline"]), "trang_thai": f"{t['status']} ({t['progress']}%)"})

    open_tasks = [t for t in data["tasks"] if t["status"] != "Hoàn thành"]
    done_in_period = {u["task_id"] for u in data["updates"] if u["status"] == "Hoàn thành"}
    stats = {
        "nhan_vien": len(data["staff"]),
        "da_nop": len({r["user_id"] for r in data["reports"]}),
        "chua_nop": len(data["missing"]),
        "so_bao_cao": len(data["reports"]),
        "viec_dang_mo": len(open_tasks),
        "viec_qua_han": len(data["overdue"]),
        "viec_xong_trong_ky": len(done_in_period),
        "van_de_cao": sum(1 for r in data["reports"] if r["issue_level"] == "Cao"),
        "tien_do_tb": round(sum(r["progress"] for r in data["reports"]) / len(data["reports"])) if data["reports"] else 0,
    }

    analysis = ai.summarize(_dataset_text(data, label)) if use_ai and data["reports"] else None
    if not analysis:
        analysis = _plain_summary(data, stats)

    title = f"BÁO CÁO TỔNG HỢP {label.upper()}" + (f" – {department.upper()}" if department else "")
    return {"title": title, "label": label, "department": department, "generated_at": db.now().strftime("%H:%M %d/%m/%Y"),
            "stats": stats, "rows": rows, "missing": data["missing"], "overdue": data["overdue"],
            "open_tasks": open_tasks, "reports": data["reports"], **analysis}


def _fmt_date(s):
    try:
        return date.fromisoformat(s).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return s or UNKNOWN


def _dataset_text(data, label):
    out = [f"KỲ BÁO CÁO: {label}", "", "BÁO CÁO NHÂN VIÊN:"]
    for r in data["reports"]:
        out.append(f"- [{r['report_date']}] {r['full_name']} ({r['department'] or 'Chưa rõ phòng'}) | tiến độ {r['progress']}%"
                   f"\n  Đã làm: {r['done']}\n  Kế hoạch: {r['plan']}\n  Vấn đề ({r['issue_level'] or 'không'}): {r['issues']}"
                   f"\n  Đề xuất: {r['proposals']}")
        for a in r.get("attachments", []):
            out.append(f"  Tệp '{a['filename']}' – chủ đề: {a.get('subject') or 'không ghi'}:\n    " + (a["content"] or "(không có nội dung chữ)")[:4000].replace("\n", "\n    "))
    out += ["", "CHƯA NỘP BÁO CÁO: " + (", ".join(f"{m['full_name']} ({m['so_ngay_thieu']} ngày)" for m in data["missing"]) or "không")]
    out += ["", "CÔNG VIỆC QUÁ HẠN:"] + [f"- {t['title']} – {t['assignee']} – hạn {t['deadline']} – {t['progress']}%" for t in data["overdue"]]
    out += ["", "CÔNG VIỆC ĐANG MỞ:"] + [f"- {t['title']} – {t['assignee']} – hạn {t['deadline'] or 'chưa có'} – {t['status']} {t['progress']}%"
                                       for t in data["tasks"] if t["status"] != "Hoàn thành"][:80]
    return "\n".join(out)


def _plain_summary(data, s):
    """Tóm tắt không dùng AI (khi chưa có OPENAI_API_KEY hoặc không có báo cáo)."""
    parts = [f"{s['da_nop']}/{s['nhan_vien']} nhân viên đã nộp báo cáo ({s['so_bao_cao']} báo cáo)."]
    if data["reports"]:
        parts.append(f"Tiến độ trung bình {s['tien_do_tb']}%.")
    parts.append(f"{s['viec_dang_mo']} công việc đang mở, {s['viec_qua_han']} việc quá hạn.")
    if s["van_de_cao"]:
        parts.append(f"Có {s['van_de_cao']} vấn đề mức CAO cần sếp chú ý.")
    by_dep = {}
    for r in data["reports"]:
        by_dep.setdefault(r["department"] or "Chưa phân phòng", []).append(r)
    deps = [{"ten": k, "tom_tat": f"{len(v)} báo cáo, tiến độ TB {round(sum(x['progress'] for x in v) / len(v))}%. "
                                  + "; ".join(f"{x['full_name']}: {x['done'][:80]}" for x in v[:4])} for k, v in by_dep.items()]
    risks = [f"{r['full_name']}: {r['issues']}" for r in data["reports"] if r["issue_level"] == "Cao" and r["issues"]]
    risks += [f"Quá hạn: {t['title']} ({t['assignee']})" for t in data["overdue"]]
    return {"tom_tat": " ".join(parts), "theo_phong_ban": deps, "rui_ro": risks[:10], "goi_y": [], "ai": False}


# ---------- Văn bản gửi Telegram ----------
def to_message(rep, detail=True):
    s = rep["stats"]
    L = [f"📊 {rep['title']}", f"🕒 Lập lúc {rep['generated_at']}" + ("" if rep.get("ai") else " · chế độ cơ bản (chưa bật AI)"), "",
         f"👥 Đã nộp: {s['da_nop']}/{s['nhan_vien']}   📈 Tiến độ TB: {s['tien_do_tb']}%",
         f"📌 Việc đang mở: {s['viec_dang_mo']}   ⏰ Quá hạn: {s['viec_qua_han']}   ⚠️ Vấn đề cao: {s['van_de_cao']}",
         "", "📝 TÓM TẮT", rep["tom_tat"]]
    if detail and rep.get("theo_phong_ban"):
        L += ["", "🏢 THEO PHÒNG BAN"] + [f"• {d['ten']}: {d['tom_tat']}" for d in rep["theo_phong_ban"]]
    if rep.get("rui_ro"):
        L += ["", "🚨 RỦI RO / CẦN XỬ LÝ"] + [f"• {x}" for x in rep["rui_ro"][:8]]
    if rep.get("goi_y"):
        L += ["", "💡 GỢI Ý CHO SẾP"] + [f"• {x}" for x in rep["goi_y"][:5]]
    if rep["missing"]:
        L += ["", f"❌ CHƯA NỘP BÁO CÁO ({len(rep['missing'])})"] + [
            f"• {m['full_name']} – {m['department'] or 'Chưa rõ phòng'}" + (f" (thiếu {m['so_ngay_thieu']} ngày)" if m["so_ngay_thieu"] > 1 else "")
            for m in rep["missing"][:20]]
    return "\n".join(L)
