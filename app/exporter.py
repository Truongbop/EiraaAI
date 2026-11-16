"""Xuất báo cáo tổng hợp ra Excel / Word / PDF / TXT."""
import os
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Cm
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from . import db
from .config import EXPORT_DIR

HEAD = ["STT", "Hạng mục", "Nội dung", "Người phụ trách", "Thời hạn", "Trạng thái"]


def export(rep, formats):
    stamp = db.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(2).hex()
    out = []
    for f in dict.fromkeys(formats or []):
        fn = {"xlsx": to_xlsx, "docx": to_docx, "pdf": to_pdf, "txt": to_txt}.get(f)
        if fn:
            path = EXPORT_DIR / f"BaoCao_{stamp}.{f}"
            fn(rep, path)
            out.append(str(path))
    return out


def _stat_lines(rep):
    if rep.get("stat_lines"):
        return list(rep["stat_lines"])
    s = rep["stats"]
    return [("Nhân viên", s["nhan_vien"]), ("Đã nộp báo cáo", s["da_nop"]), ("Chưa nộp", s["chua_nop"]),
            ("Tiến độ trung bình (%)", s["tien_do_tb"]), ("Việc đang mở", s["viec_dang_mo"]),
            ("Việc quá hạn", s["viec_qua_han"]), ("Vấn đề mức cao", s["van_de_cao"])]


def _is_msg(rep):
    return rep.get("kind") == "tin_nhan"


def _table_rows(rep):
    return [[i, r["hang_muc"], r["noi_dung"], r["nguoi"], r["thoi_han"], r["trang_thai"]] for i, r in enumerate(rep["rows"], 1)]


# ---------------- Excel ----------------
FILL = PatternFill("solid", fgColor="1F4E78")
THIN = Side(style="thin", color="B7B7B7")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CAT_FILL = {"Vấn đề": "FDE9E7", "Quá hạn": "FCE4D6", "Đề xuất": "FFF2CC", "Công việc": "FFFFFF",
            "Tiến độ": "EAF2FB", "Báo cáo": "E9F7EF", "Chưa nộp": "FCE4D6", "Tin nhắn – việc": "FFFFFF"}


def _sheet(ws, headers, data, widths, color_col=None):
    ws.append(headers)
    for c in ws[ws.max_row]:
        c.fill, c.font, c.border = FILL, Font(bold=True, color="FFFFFF"), BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    start = ws.max_row
    for row in data:
        ws.append(row)
        for c in ws[ws.max_row]:
            c.alignment, c.border = Alignment(wrap_text=True, vertical="top"), BOX
            if color_col is not None:
                c.fill = PatternFill("solid", fgColor=CAT_FILL.get(row[color_col], "FFFFFF"))
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = ws.cell(row=start + 1, column=1)


def to_xlsx(rep, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Tổng hợp"
    ws.append([rep["title"]])
    ws["A1"].font = Font(bold=True, size=14, color="1F4E78")
    ws.append([f"Lập lúc: {rep['generated_at']}  –  Nguồn: {rep.get('source', 'báo cáo nhân viên trên hệ thống')}"])
    ws.append([])
    for k, v in _stat_lines(rep):
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.append([])
    ws.append(["Tóm tắt"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.append([rep["tom_tat"]])
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row + 5, end_column=6)
    ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 14

    _sheet(wb.create_sheet("Chi tiết"), HEAD, _table_rows(rep), [6, 12, 70, 26, 14, 26], color_col=1)
    if _is_msg(rep):
        for name, headers, data, widths in rep.get("extra_sheets", []):
            _sheet(wb.create_sheet(name), headers, data, widths)
        wb.save(path)
        return
    _sheet(wb.create_sheet("Báo cáo nhân viên"),
           ["Ngày", "Nhân viên", "Phòng ban", "Đã làm", "Tiến độ %", "Kế hoạch", "Vấn đề", "Mức độ", "Đề xuất", "Tệp"],
           [[r["report_date"], r["full_name"], r["department"], r["done"], r["progress"], r["plan"], r["issues"],
             r["issue_level"], r["proposals"], ", ".join(a["filename"] for a in r.get("attachments", []))] for r in rep["reports"]],
           [11, 20, 16, 50, 9, 40, 40, 11, 35, 25])
    _sheet(wb.create_sheet("Công việc đang mở"), ["Công việc", "Phụ trách", "Phòng ban", "Hạn", "Trạng thái", "Tiến độ %"],
           [[t["title"], t["assignee"], t["department"], t["deadline"], t["status"], t["progress"]] for t in rep["open_tasks"]],
           [50, 22, 16, 12, 14, 10])
    _sheet(wb.create_sheet("Chưa nộp"), ["Nhân viên", "Phòng ban", "Số ngày thiếu", "Ngày thiếu"],
           [[m["full_name"], m["department"], m["so_ngay_thieu"], m["ngay_thieu"]] for m in rep["missing"]], [26, 18, 14, 40])
    wb.save(path)


# ---------------- Word ----------------
def to_docx(rep, path):
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Times New Roman", Pt(12)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(rep["title"])
    r.bold, r.font.size, r.font.color.rgb = True, Pt(16), RGBColor(0x1F, 0x4E, 0x78)
    p = doc.add_paragraph(f"Lập lúc: {rep['generated_at']}")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].italic = True

    doc.add_heading("I. Số liệu chung", 2)
    for k, v in _stat_lines(rep):
        doc.add_paragraph(f"{k}: {v}", style="List Bullet")

    doc.add_heading("II. Tóm tắt", 2)
    doc.add_paragraph(rep["tom_tat"])
    if rep.get("theo_phong_ban"):
        doc.add_heading("III. " + rep.get("group_label", "Theo phòng ban"), 2)
        for d in rep["theo_phong_ban"]:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(d["ten"] + ": ").bold = True
            p.add_run(d["tom_tat"])

    doc.add_heading("IV. Chi tiết công việc – vấn đề – đề xuất", 2)
    rows = _table_rows(rep)
    if rows:
        t = doc.add_table(rows=1, cols=len(HEAD))
        t.style = "Table Grid"
        for i, h in enumerate(HEAD):
            c = t.rows[0].cells[i]
            c.text = h
            c.paragraphs[0].runs[0].bold = True
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = str(v)
        for row in t.rows:
            for i, w in enumerate([1.0, 2.2, 6.5, 3.2, 2.2, 3.0]):
                row.cells[i].width = Cm(w)
    else:
        doc.add_paragraph("Chưa có dữ liệu trong kỳ.")

    if rep.get("rui_ro"):
        doc.add_heading("V. Rủi ro / cần xử lý", 2)
        for x in rep["rui_ro"]:
            doc.add_paragraph(x, style="List Bullet")
    if rep.get("goi_y"):
        doc.add_heading("VI. " + ("Thông tin quan trọng" if _is_msg(rep) else "Gợi ý"), 2)
        for x in rep["goi_y"]:
            doc.add_paragraph(x, style="List Bullet")
    if rep["missing"]:
        doc.add_heading("VII. Nhân viên chưa nộp báo cáo", 2)
        for m in rep["missing"]:
            doc.add_paragraph(f"{m['full_name']} – {m['department'] or 'Chưa rõ phòng'} (thiếu {m['so_ngay_thieu']} ngày: {m['ngay_thieu']})",
                              style="List Bullet")
    doc.save(path)


# ---------------- PDF ----------------
_FONT = None


def _font():
    global _FONT
    if _FONT:
        return _FONT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    windir = os.environ.get("WINDIR", r"C:\Windows")
    pairs = [(Path(windir, "Fonts", "arial.ttf"), Path(windir, "Fonts", "arialbd.ttf")),
             (Path(windir, "Fonts", "times.ttf"), Path(windir, "Fonts", "timesbd.ttf")),
             (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
             (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
              Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"))]
    for reg, bold in pairs:
        if reg.exists():
            pdfmetrics.registerFont(TTFont("VN", str(reg)))
            pdfmetrics.registerFont(TTFont("VN-B", str(bold if bold.exists() else reg)))
            _FONT = ("VN", "VN-B")
            return _FONT
    _FONT = ("Helvetica", "Helvetica-Bold")   # không có font tiếng Việt
    return _FONT


def to_pdf(rep, path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    reg, bold = _font()
    S = lambda size=10, font=reg, **kw: ParagraphStyle("s", fontName=font, fontSize=size, leading=size * 1.35, **kw)
    P = lambda txt, st=None: Paragraph(escape(str(txt)).replace("\n", "<br/>"), st or S())
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=rep["title"])
    el = [P(rep["title"], S(16, bold, alignment=1, textColor=colors.HexColor("#1F4E78"))),
          P(f"Lập lúc: {rep['generated_at']}", S(9, alignment=1, textColor=colors.grey)), Spacer(1, 10),
          P("  ·  ".join(f"{k}: {v}" for k, v in _stat_lines(rep)), S(10, bold)), Spacer(1, 8),
          P("Tóm tắt", S(12, bold)), P(rep["tom_tat"]), Spacer(1, 8)]
    if rep.get("theo_phong_ban"):
        el.append(P(rep.get("group_label", "Theo phòng ban"), S(12, bold)))
    for d in rep.get("theo_phong_ban") or []:
        el.append(P(f"• {d['ten']}: {d['tom_tat']}"))
    if rep.get("rui_ro"):
        el += [Spacer(1, 6), P("Rủi ro / cần xử lý", S(12, bold))] + [P("• " + x) for x in rep["rui_ro"]]
    el += [Spacer(1, 10), P("Chi tiết", S(12, bold))]
    data = [[P(h, S(9, bold, textColor=colors.white)) for h in HEAD]] + \
           [[P(c, S(9)) for c in row] for row in _table_rows(rep)]
    if len(data) == 1:
        data.append([P("")] * 2 + [P("Chưa có dữ liệu trong kỳ.")] + [P("")] * 3)
    t = Table(data, colWidths=[1.1 * cm, 2.3 * cm, 12 * cm, 5 * cm, 2.6 * cm, 3.6 * cm], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B7B7B7")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FB")])]))
    el.append(t)
    if rep["missing"]:
        el += [Spacer(1, 10), P(f"Chưa nộp báo cáo ({len(rep['missing'])})", S(12, bold))] + \
              [P(f"• {m['full_name']} – {m['department'] or 'Chưa rõ phòng'} (thiếu {m['so_ngay_thieu']} ngày)") for m in rep["missing"]]
    doc.build(el)


# ---------------- TXT ----------------
def to_txt(rep, path):
    if _is_msg(rep):
        from .tgmsg import to_message
    else:
        from .summary import to_message
    lines = [to_message(rep), "", "CHI TIẾT:"] + [f"{r[0]}. [{r[1]}] {r[2]} | {r[3]} | {r[4]} | {r[5]}" for r in _table_rows(rep)]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
