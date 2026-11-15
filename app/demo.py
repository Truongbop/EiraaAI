"""Dữ liệu demo: tự nạp ở lần chạy đầu tiên khi hệ thống chưa có nhân viên nào.

Muốn tắt: thêm DEMO_DATA=0 vào file .env (rồi xóa thư mục data để làm sạch).
"""
import logging
import random
from datetime import timedelta

from docx import Document
from openpyxl import Workbook

from . import db, files

log = logging.getLogger("eiraa.demo")

STAFF = [
    ("Nguyễn Thị Lan", "Kinh doanh", "Nhân viên kinh doanh"),
    ("Trần Văn Tuấn", "Kinh doanh", "Trưởng nhóm kinh doanh"),
    ("Lê Minh Hùng", "Kỹ thuật", "Lập trình viên"),
    ("Phạm Thu Phương", "Kỹ thuật", "Kiểm thử phần mềm"),
    ("Đỗ Ngọc Mai", "Kế toán", "Kế toán viên"),
    ("Vũ Đức Anh", "Marketing", "Chuyên viên marketing"),
    ("Hoàng Thị Hà", "Nhân sự", "Chuyên viên nhân sự"),
]

CUSTOMERS = ["Minh Phát", "An Khang", "Hòa Bình", "Sao Việt", "Đại Dương", "Phú Thành"]


# ---------- Tạo nội dung file Excel / Word ----------
def _xlsx_sales(name, day, rnd):
    wb = Workbook()
    ws = wb.active
    ws.title = "Doanh số"
    ws.append([f"BÁO CÁO KINH DOANH NGÀY {day:%d/%m/%Y} – {name}"])
    ws.append([])
    ws.append(["Khách hàng", "Sản phẩm", "Giá trị (VNĐ)", "Trạng thái", "Ghi chú"])
    total = 0
    for c in rnd.sample(CUSTOMERS, 3):
        v = rnd.randint(15, 180) * 1_000_000
        st = rnd.choice(["Đã chốt", "Đang đàm phán", "Đã gửi báo giá", "Chờ duyệt chiết khấu"])
        total += v if st == "Đã chốt" else 0
        ws.append([c, rnd.choice(["Phần mềm bán hàng", "Gói bảo trì", "Thiết bị POS", "Gói đào tạo"]), v, st,
                   "Khách yêu cầu giảm 5%" if st == "Chờ duyệt chiết khấu" else ""])
    ws.append([])
    ws.append(["Tổng doanh số đã chốt", "", total])
    ws.append(["Khó khăn", rnd.choice(["Khách hàng so sánh giá với đối thủ", "Chưa có", "Một khách chậm thanh toán"])])
    return wb


def _xlsx_finance(day, rnd):
    wb = Workbook()
    ws = wb.active
    ws.title = "Thu chi"
    ws.append([f"BẢNG THU CHI NGÀY {day:%d/%m/%Y}"])
    ws.append(["Khoản mục", "Thu (VNĐ)", "Chi (VNĐ)", "Ghi chú"])
    ws.append(["Thu tiền khách hàng", rnd.randint(50, 300) * 1_000_000, 0, ""])
    ws.append(["Thanh toán nhà cung cấp", 0, rnd.randint(20, 120) * 1_000_000, "3 hóa đơn"])
    ws.append(["Chi phí văn phòng", 0, rnd.randint(2, 15) * 1_000_000, ""])
    ws.append(["Công nợ quá hạn", "", "", rnd.choice(["Không có", "Công ty Sao Việt nợ 45 triệu quá 20 ngày"])])
    return wb


def _docx(title, paragraphs):
    d = Document()
    d.add_heading(title, 1)
    for p in paragraphs:
        if p.startswith("- "):
            d.add_paragraph(p[2:], style="List Bullet")
        else:
            d.add_paragraph(p)
    return d


def _tech_doc(name, day, rnd):
    pct = rnd.choice([40, 55, 70, 85, 100])
    issue = rnd.choice(["Server chậm vào giờ cao điểm, khách phản ánh không thanh toán được.",
                        "Chưa có vấn đề.", "Thiếu tài liệu API từ đối tác ngân hàng."])
    return _docx(f"Báo cáo tiến độ kỹ thuật {day:%d/%m/%Y} – {name}", [
        "Công việc đã làm:",
        f"- Phát triển tính năng thanh toán online: hoàn thành {pct}%",
        f"- Sửa {rnd.randint(2, 8)} lỗi trên web bán hàng, deploy bản 2.{rnd.randint(3, 6)}",
        f"- Viết và chạy {rnd.randint(20, 60)} ca kiểm thử",
        f"Vấn đề: {issue}",
        "Kế hoạch ngày mai: tối ưu tốc độ trang thanh toán, kiểm thử hồi quy.",
    ])


def _marketing_doc(name, day, rnd):
    return _docx(f"Báo cáo marketing {day:%d/%m/%Y} – {name}", [
        f"- Chạy quảng cáo Facebook: {rnd.randint(8, 30)} triệu chi phí, {rnd.randint(40, 200)} khách để lại thông tin",
        f"- Đăng {rnd.randint(2, 6)} bài viết, tương tác tăng {rnd.randint(5, 40)}%",
        "- Chuẩn bị chương trình khuyến mãi cuối tháng",
        f"Đề xuất: {rnd.choice(['Tăng ngân sách quảng cáo thêm 10 triệu', 'Thuê KOL giới thiệu sản phẩm', 'Không có'])}",
    ])


def _hr_doc(name, day, rnd):
    return _docx(f"Báo cáo nhân sự {day:%d/%m/%Y} – {name}", [
        f"- Phỏng vấn {rnd.randint(1, 5)} ứng viên vị trí nhân viên kinh doanh",
        f"- Xử lý {rnd.randint(1, 4)} đơn nghỉ phép, chấm công tuần",
        f"- Tiến độ tuyển dụng: đã tuyển {rnd.randint(0, 2)}/2 người",
        f"Vấn đề: {rnd.choice(['Ứng viên chất lượng thấp', 'Chưa có'])}",
    ])


def _save(obj, filename):
    """Lưu workbook/document vào thư mục uploads và đọc lại nội dung chữ."""
    import io
    buf = io.BytesIO()
    obj.save(buf)
    data = buf.getvalue()
    path, text = files.save_upload(filename, data)
    return str(path), len(data), text


def seed():
    rnd = random.Random(2026)
    T = db.today()
    for name, dep, pos in STAFF:
        db.create_staff(name, dep, pos)
    users = {u["full_name"]: u for u in db.list_users(active_only=True) if u["role"] == "staff"}

    for back in range(6, -1, -1):
        day = T - timedelta(days=back)
        if day.weekday() == 6 and back > 0:          # Chủ nhật nghỉ (riêng hôm nay luôn có dữ liệu)
            continue
        for name, dep, _ in STAFF:
            # Hôm nay để 2 người chưa nộp, các ngày trước thỉnh thoảng có người quên nộp
            if (back == 0 and name in ("Trần Văn Tuấn", "Hoàng Thị Hà")) or (back > 0 and rnd.random() < 0.12):
                continue
            uid = users[name]["id"]
            short = name.split()[-1]
            if dep == "Kinh doanh":
                obj, subject, fn = _xlsx_sales(name, day, rnd), f"Báo cáo kinh doanh ngày {day:%d/%m}", f"KinhDoanh_{short}_{day:%d%m}.xlsx"
            elif dep == "Kế toán":
                obj, subject, fn = _xlsx_finance(day, rnd), f"Bảng thu chi ngày {day:%d/%m}", f"ThuChi_{day:%d%m}.xlsx"
            elif dep == "Kỹ thuật":
                obj, subject, fn = _tech_doc(name, day, rnd), f"Tiến độ kỹ thuật ngày {day:%d/%m}", f"KyThuat_{short}_{day:%d%m}.docx"
            elif dep == "Marketing":
                obj, subject, fn = _marketing_doc(name, day, rnd), f"Báo cáo marketing ngày {day:%d/%m}", f"Marketing_{day:%d%m}.docx"
            else:
                obj, subject, fn = _hr_doc(name, day, rnd), f"Báo cáo nhân sự ngày {day:%d/%m}", f"NhanSu_{day:%d%m}.docx"
            path, size, text = _save(obj, fn)
            db.submit_file(uid, subject, fn, path, size, text, report_date=day)
            rep = db.get_report(uid, day.isoformat())
            prog = rnd.choice([60, 70, 80, 90, 100])
            issues, level, prop = "", "", ""
            if name == "Lê Minh Hùng" and back == 0:
                issues, level, prop = "Server chậm giờ cao điểm, khách không thanh toán được", "Cao", "Nâng cấp RAM server lên 16GB"
            elif name == "Nguyễn Thị Lan" and back <= 1:
                issues, level, prop = "Khách Minh Phát yêu cầu giảm 5% và kéo dài thời hạn thanh toán", "Trung bình", "Xin duyệt mức giảm 3%"
            db.upsert_report(uid, day.isoformat(), rep["done"], prog, "", issues, level, prop)
            with db.conn() as c:            # giờ nộp: cuối buổi chiều ngày đó
                c.execute("UPDATE attachments SET created_at=? WHERE report_id=?",
                          (f"{day.isoformat()}T{rnd.randint(16, 17)}:{rnd.randint(10, 59)}:00+07:00", rep["id"]))

    tasks = [("Ký hợp đồng khách Minh Phát", "Nguyễn Thị Lan", 3, "Đang làm", 60),
             ("Báo cáo doanh số tháng", "Trần Văn Tuấn", -1, "Đang làm", 40),
             ("Tuyển 2 nhân viên kinh doanh", "Hoàng Thị Hà", -3, "Đang làm", 30),
             ("Nâng cấp server bán hàng", "Lê Minh Hùng", 2, "Chưa làm", 0),
             ("Tính năng thanh toán online", "Lê Minh Hùng", 7, "Đang làm", 70),
             ("Kiểm thử bản 2.5", "Phạm Thu Phương", 0, "Hoàn thành", 100),
             ("Báo cáo thuế quý 4", "Đỗ Ngọc Mai", 10, "Chưa làm", 0),
             ("Thu hồi công nợ Sao Việt", "Đỗ Ngọc Mai", -2, "Đang làm", 50),
             ("Chương trình khuyến mãi cuối tháng", "Vũ Đức Anh", 5, "Đang làm", 35)]
    for title, who, dl, st, pr in tasks:
        tid = db.create_task(title, "", users[who]["id"], (T + timedelta(days=dl)).isoformat(), None)
        db.update_task(tid, None, status=st, progress=pr)
    db.log("hệ thống", "nạp dữ liệu demo", f"{len(STAFF)} nhân viên, {len(tasks)} công việc")
    log.info("Đã nạp dữ liệu demo: %s nhân viên, báo cáo 7 ngày, %s công việc", len(STAFF), len(tasks))


def seed_if_empty():
    staff = [u for u in db.list_users() if u["role"] == "staff"]
    if not staff and not db.get_setting("demo_seeded"):
        seed()
        db.set_setting("demo_seeded", "1")
