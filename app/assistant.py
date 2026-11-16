"""Bộ não trả lời sếp trên Telegram: hiểu câu hỏi → tra dữ liệu → trả lời + file đính kèm."""
import re
from dataclasses import dataclass, field

from . import ai, db, exporter, summary, tgmsg

ALL_FORMATS = ["xlsx", "docx", "pdf"]


@dataclass
class Reply:
    text: str
    files: list = field(default_factory=list)   # đường dẫn file cần gửi kèm


# ---------- Các "công cụ" tra cứu dữ liệu ----------
def tool_report(ky="hôm nay", phong_ban=None, xuat_file=None, _files=None):
    start, end, label = summary.parse_period(ky)
    rep = summary.build(start, end, label, phong_ban or None)
    if xuat_file and _files is not None:
        _files += exporter.export(rep, xuat_file)
    return summary.to_message(rep)


def tool_missing(ky="hôm nay", phong_ban=None):
    start, end, label = summary.parse_period(ky)
    data = summary.collect(start, end, phong_ban or None)
    if not data["missing"]:
        return f"✅ Tất cả nhân viên đã nộp báo cáo {label}."
    return f"❌ Chưa nộp báo cáo {label} ({len(data['missing'])}/{len(data['staff'])}):\n" + "\n".join(
        f"• {m['full_name']} – {m['department'] or 'Chưa rõ phòng'}" + (f" – thiếu {m['so_ngay_thieu']} ngày: {m['ngay_thieu']}"
                                                                      if m["so_ngay_thieu"] > 1 else "")
        for m in data["missing"])


def tool_tasks(trang_thai=None, qua_han=False, nhan_vien=None, phong_ban=None):
    tasks = db.list_tasks(status=trang_thai or None, department=phong_ban or None, overdue=bool(qua_han))
    if nhan_vien:
        tasks = [t for t in tasks if t["assignee"] and nhan_vien.lower() in t["assignee"].lower()]
    if not trang_thai and not qua_han:
        tasks = [t for t in tasks if t["status"] != "Hoàn thành"]
    if not tasks:
        return "Không có công việc nào khớp điều kiện."
    head = "⏰ CÔNG VIỆC QUÁ HẠN" if qua_han else "📌 CÔNG VIỆC"
    return f"{head} ({len(tasks)}):\n" + "\n".join(
        f"{i}. {t['title']} – {t['assignee'] or 'Chưa giao'} – hạn {summary._fmt_date(t['deadline'])} – {t['status']} {t['progress']}%"
        for i, t in enumerate(tasks[:40], 1))


def tool_staff(ten, ky="7 ngày"):
    users = [u for u in db.list_users() if ten.lower() in u["full_name"].lower() or ten.lower() == u["username"].lower()]
    if not users:
        return f"Không tìm thấy nhân viên tên '{ten}'."
    start, end, label = summary.parse_period(ky)
    out = []
    for u in users[:3]:
        reps = db.list_reports(start, end, user_id=u["id"])
        tasks = [t for t in db.list_tasks(assignee_id=u["id"]) if t["status"] != "Hoàn thành"]
        out.append(f"👤 {u['full_name']} – {u['department'] or 'Chưa rõ phòng'} {('– ' + u['position']) if u['position'] else ''}")
        out.append(f"Báo cáo {label}: {len(reps)}")
        for r in reps[:7]:
            out.append(f"  [{r['report_date'][8:]}/{r['report_date'][5:7]}] {r['progress']}% – {r['done'][:200]}"
                       + (f"\n     ⚠️ {r['issues'][:150]}" if r["issues"] else ""))
        out.append(f"Việc đang làm: {len(tasks)}")
        out += [f"  • {t['title']} – hạn {summary._fmt_date(t['deadline'])} – {t['progress']}%" for t in tasks[:10]]
    return "\n".join(out)


def tool_attachments(ten=None, ky="hôm nay", _files=None):
    start, end, label = summary.parse_period(ky)
    reps = db.list_reports(start, end)
    if ten:
        reps = [r for r in reps if ten.lower() in r["full_name"].lower()]
    found = [(r, a) for r in reps for a in r["attachments"]]
    if not found:
        return f"Không có file đính kèm nào {label}" + (f" của {ten}" if ten else "") + "."
    if _files is not None:
        _files += [(a["stored_path"], f"{r['full_name']} - {a['filename']}") for r, a in found[:10]]
    return f"📎 Có {len(found)} file đính kèm {label}" + (" (gửi 10 file đầu)" if len(found) > 10 else "") + ":\n" + "\n".join(
        f"• {r['full_name']} [{r['report_date'][8:]}/{r['report_date'][5:7]}]: {a.get('subject') or a['filename']} ({a['filename']})"
        for r, a in found[:30])


def is_message_question(t):
    return bool(re.search(r"tin nhắn|tin nhan|\bnt\b|\btn\b|nhóm chat|nhóm tele|\btele\b|telegram|trao đổi|\bchat\b|\bgroup\b|\bnhóm\b", t))


def tool_messages(ky="hôm nay", nhom=None, xuat_file=None, _files=None):
    chat_ids = tgmsg.find_chats(nhom) if nhom else None
    rep = tgmsg.build(ky + (" " + nhom if nhom else ""), chat_ids)
    if xuat_file and _files is not None:
        _files += exporter.export(rep, xuat_file)
    return tgmsg.to_message(rep)


def tool_overview():
    d = db.dashboard(db.today())
    deps = db.departments()
    return (f"Hôm nay {db.today():%d/%m/%Y}: {d['submitted']}/{d['staff']} nhân viên đã nộp báo cáo; "
            f"{d['tasks_open']} việc đang mở, {d['tasks_overdue']} quá hạn, {d['tasks_done']} đã xong; "
            f"{d['issues_high']} vấn đề mức cao. Phòng ban: {', '.join(deps) or 'chưa có'}.")


TOOLS = [
    {"type": "function", "function": {
        "name": "tong_hop_bao_cao",
        "description": "Tổng hợp báo cáo công việc của nhân viên trong một kỳ. Dùng khi sếp muốn tổng hợp/tóm tắt/báo cáo tình hình. "
                       "Nếu sếp muốn file (excel, word, pdf, gửi file, xuất) thì điền xuat_file.",
        "parameters": {"type": "object", "properties": {
            "ky": {"type": "string", "description": "hôm nay | hôm qua | tuần này | tuần trước | tháng này | tháng trước | dd/mm/yyyy | N ngày"},
            "phong_ban": {"type": "string", "description": "Tên phòng ban nếu sếp chỉ định"},
            "xuat_file": {"type": "array", "items": {"type": "string", "enum": ["xlsx", "docx", "pdf", "txt"]}}},
            "required": ["ky"]}}},
    {"type": "function", "function": {
        "name": "danh_sach_chua_nop", "description": "Liệt kê nhân viên chưa nộp báo cáo trong kỳ.",
        "parameters": {"type": "object", "properties": {"ky": {"type": "string"}, "phong_ban": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "cong_viec", "description": "Tra cứu công việc được giao: theo trạng thái, quá hạn, nhân viên, phòng ban.",
        "parameters": {"type": "object", "properties": {
            "trang_thai": {"type": "string", "enum": db.STATUSES}, "qua_han": {"type": "boolean"},
            "nhan_vien": {"type": "string"}, "phong_ban": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "bao_cao_nhan_vien", "description": "Xem báo cáo và công việc của một nhân viên cụ thể.",
        "parameters": {"type": "object", "properties": {"ten": {"type": "string"}, "ky": {"type": "string"}}, "required": ["ten"]}}},
    {"type": "function", "function": {
        "name": "gui_tep_bao_cao", "description": "Gửi về Telegram các file gốc (Excel/Word/PDF...) mà nhân viên đính kèm vào báo cáo.",
        "parameters": {"type": "object", "properties": {"ten": {"type": "string", "description": "Tên nhân viên, bỏ trống = tất cả"},
                                                         "ky": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "tong_hop_tin_nhan",
        "description": "Tổng hợp / tóm tắt TIN NHẮN TRAO ĐỔI trong các nhóm Telegram: phân loại theo nhóm công việc, "
                       "đầu việc, tiến độ, vấn đề cần xử lý, đề xuất. Dùng khi sếp hỏi về tin nhắn, nhóm, trao đổi, chat.",
        "parameters": {"type": "object", "properties": {
            "ky": {"type": "string", "description": "hôm nay | hôm qua | tuần này | tháng này | dd/mm/yyyy | N ngày"},
            "nhom": {"type": "string", "description": "Tên nhóm Telegram nếu sếp chỉ định"},
            "xuat_file": {"type": "array", "items": {"type": "string", "enum": ["xlsx", "docx", "pdf", "txt"]}}},
            "required": ["ky"]}}},
    {"type": "function", "function": {
        "name": "tong_quan_hom_nay", "description": "Số liệu nhanh hôm nay và danh sách phòng ban.",
        "parameters": {"type": "object", "properties": {}}}},
]

SYSTEM = """Bạn là EIRAA AI – trợ lý riêng của giám đốc {company} trên Telegram. Hôm nay là {today}.
Nhân viên nộp báo cáo trên hệ thống quản lý và trao đổi trong các nhóm Telegram (bot ghi nhận tin nhắn nhóm);
bạn tra dữ liệu bằng công cụ rồi trả lời sếp. Câu hỏi về tin nhắn / nhóm / trao đổi → dùng tong_hop_tin_nhan.
- Luôn dùng công cụ để lấy số liệu, KHÔNG tự bịa. Thiếu dữ liệu thì nói rõ "Chưa xác định".
- Xưng "em", gọi "sếp". Trả lời tiếng Việt, gọn, rõ, dùng gạch đầu dòng; không dùng Markdown (**, #).
- Khi sếp nói "tổng hợp báo cáo", "báo cáo hôm nay/tuần/tháng" → gọi tong_hop_bao_cao. Nếu sếp nhắc file/excel/word/pdf/xuất
  thì truyền xuat_file tương ứng; "gửi file"/"xuất báo cáo" không nói rõ định dạng → ["xlsx","docx","pdf"].
- Khi công cụ tong_hop_bao_cao trả về văn bản báo cáo, hãy gửi lại gần như nguyên văn, có thể thêm 1-2 câu nhận xét.
- Câu hỏi ngoài công việc công ty: trả lời như một trợ lý ChatGPT bình thường."""


def answer(question: str, history: list | None = None) -> Reply:
    files: list = []

    def execute(name, args):
        if name == "tong_hop_bao_cao":
            return tool_report(args.get("ky") or "hôm nay", args.get("phong_ban"), args.get("xuat_file"), files)
        if name == "danh_sach_chua_nop":
            return tool_missing(args.get("ky") or "hôm nay", args.get("phong_ban"))
        if name == "cong_viec":
            return tool_tasks(args.get("trang_thai"), args.get("qua_han", False), args.get("nhan_vien"), args.get("phong_ban"))
        if name == "bao_cao_nhan_vien":
            return tool_staff(args.get("ten", ""), args.get("ky") or "7 ngày")
        if name == "gui_tep_bao_cao":
            return tool_attachments(args.get("ten"), args.get("ky") or "hôm nay", files)
        if name == "tong_hop_tin_nhan":
            return tool_messages(args.get("ky") or "hôm nay", args.get("nhom"), args.get("xuat_file"), files)
        if name == "tong_quan_hom_nay":
            return tool_overview()
        return "Không có công cụ này."

    if ai.enabled():
        try:
            sys = SYSTEM.format(company=db.get_setting("company") or "công ty", today=f"{db.today():%A %d/%m/%Y}")
            text = ai.run_agent(sys, (history or [])[-10:] + [{"role": "user", "content": question}], TOOLS, execute)
            return Reply(text, files)
        except Exception as e:
            fallback = answer_basic(question)
            fallback.text = f"(AI tạm lỗi: {e}. Em trả lời bằng chế độ cơ bản)\n\n" + fallback.text
            return fallback
    return answer_basic(question)


def wants_files(t):
    fm = [f for f, keys in (("xlsx", ("excel", "xlsx")), ("docx", ("word", "docx")), ("pdf", ("pdf",)), ("txt", ("txt",)))
          if any(k in t for k in keys)]
    if not fm and re.search(r"\b(file|xuất|tải|gửi file|đính kèm)\b", t):
        fm = list(ALL_FORMATS)
    return fm


def answer_basic(question: str) -> Reply:
    """Chế độ không cần AI: nhận diện từ khóa tiếng Việt."""
    t = question.lower()
    dep = summary.find_department(t)
    files: list = []
    if re.search(r"file (gốc|đính kèm|nhân viên)|tệp đính kèm|file của", t):
        m = re.search(r"của\s+([A-Za-zÀ-ỹ ]{2,30}?)(?:\s+(?:hôm|tuần|tháng|ngày)|$)", question)
        text = tool_attachments(m[1].strip() if m else None, t, files)
        return Reply(text, files)
    if is_message_question(t) and not re.search(r"file (gốc|đính kèm)", t):
        nhom = None
        if m := re.search(r"nhóm\s+([A-Za-zÀ-ỹ0-9 ]{2,40}?)(?:\s+(?:hôm|tuần|tháng|ngày|gửi|xuất)|$)", t):
            nhom = m[1].strip()
        text = tool_messages(t, nhom, wants_files(t), files)
        return Reply(text, files)
    if "chưa nộp" in t or "chưa báo cáo" in t or "ai chưa" in t:
        return Reply(tool_missing(t, dep))
    if "quá hạn" in t or "trễ hạn" in t:
        return Reply(tool_tasks(qua_han=True, phong_ban=dep))
    if re.search(r"công việc|đầu việc|tiến độ|đang làm", t) and "báo cáo" not in t:
        return Reply(tool_tasks(phong_ban=dep))
    if m := re.search(r"(?:của|nhân viên)\s+([A-Za-zÀ-ỹ ]{2,30})", question):
        name = m[1].strip()
        if any(name.lower() in u["full_name"].lower() for u in db.list_users()):
            return Reply(tool_staff(name, t))
    if re.search(r"tổng hợp|báo cáo|tóm tắt|tình hình|thống kê", t) or wants_files(t):
        text = tool_report(t, dep, wants_files(t), files)
        return Reply(text, files)
    return Reply("Em chưa hiểu yêu cầu. Sếp có thể nhắn:\n"
                 "• tổng hợp báo cáo hôm nay / tuần này / tháng này\n• gửi file excel báo cáo tuần\n"
                 "• ai chưa nộp báo cáo\n• việc nào quá hạn\n• báo cáo của <tên nhân viên>\n"
                 "• gửi file đính kèm của <tên> hôm nay\n"
                 "• tổng hợp tin nhắn hôm nay / tóm tắt nhóm kinh doanh tuần này\n"
                 "(Bật OPENAI_API_KEY để em hiểu mọi câu hỏi tự nhiên.)")
