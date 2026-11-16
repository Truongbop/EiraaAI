# EIRAA AI – Web báo cáo công việc + Trợ lý Telegram cho sếp

```
WEB (dùng chung, không cần đăng nhập)
  chọn tên + chủ đề → kéo thả file Excel / Word báo cáo, cập nhật tiến độ công việc
                          │
                PYTHON (FastAPI + SQLite)
                          │
                OPENAI (ChatGPT) – đọc báo cáo + nội dung file, tóm tắt
                          │
TELEGRAM của sếp ◄── "tổng hợp báo cáo hôm nay" → tóm tắt + Excel / Word / PDF
```

## Chạy bằng VS Code (Windows)
1. Mở thư mục có chứa `main.py` bằng **File → Open Folder**.
2. Mở terminal (Ctrl + `). **Lần đầu** gõ lần lượt:
   ```
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   copy .env.example .env
   ```
   Nếu báo lỗi "running scripts is disabled", chạy `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` rồi kích hoạt lại.
3. Mở file `.env`, điền `TELEGRAM_BOT_TOKEN` (lấy từ @BotFather) và `OPENAI_API_KEY` (nếu có).
4. Chạy:
   ```
   python main.py
   ```
   Chrome tự mở **http://localhost:8000**. Nếu không tự mở, gõ địa chỉ đó vào Chrome.
   Giữ terminal mở; bấm **Ctrl + C** để tắt.

**Những lần sau** chỉ cần:
```
.\.venv\Scripts\Activate.ps1
python main.py
```

## Dữ liệu demo
Lần đầu chạy (chưa có nhân viên nào), hệ thống tự nạp dữ liệu demo: 7 nhân viên, các file báo cáo Excel/Word trong 7 ngày gần nhất và 9 công việc.
Khi dùng thật: tắt `main.py`, thêm dòng `DEMO_DATA=0` vào `.env`, xóa thư mục `data`, rồi chạy lại.

## Mọi người vào web bằng địa chỉ nào?
- Trên máy đang chạy: `http://localhost:8000`
- Máy khác cùng mạng công ty: `http://<IP-máy-chạy>:8000`, ví dụ `http://192.168.1.20:8000`.
  Xem IP bằng lệnh `ipconfig` (dòng IPv4). Lần đầu chạy, nếu Windows hỏi tường lửa thì bấm **Allow**.

## Web có gì
- **Nộp báo cáo**: chọn tên, gõ chủ đề, kéo thả file Excel hoặc Word → bấm Nộp. Bên dưới hiện danh sách báo cáo đã nộp kèm chủ đề.
- **Tổng quan**: ai đã nộp / chưa nộp, vấn đề trong ngày, toàn bộ báo cáo.
- **Công việc**: giao việc, đổi trạng thái và tiến độ ngay trong bảng.
- **Lịch sử báo cáo**, **Nhân viên** (thêm, sửa, đánh dấu đã nghỉ), **Nhật ký**.

## Kết nối Telegram cho sếp
1. Điền `TELEGRAM_BOT_TOKEN` vào `.env`, chạy `python main.py`.
2. Sếp mở bot trên Telegram, bấm **Start**. **Người đầu tiên bấm Start sẽ thành sếp**, người khác nhắn bot bị chặn.
3. Thêm người nhận báo cáo: người đó nhắn `/id` cho bot, điền ID vào `BOSS_TELEGRAM_IDS` trong `.env`, chạy lại.

## Tổng hợp tin nhắn nhóm Telegram
1. @BotFather → `/setprivacy` → chọn bot → **Disable** (để bot đọc được mọi tin nhắn nhóm).
2. Thêm bot vào các nhóm Telegram của công ty (xóa và thêm lại nếu bot đã ở trong nhóm từ trước).
3. Bot tự ghi nhận tin nhắn + file Excel/Word gửi vào nhóm.
4. Sếp nhắn riêng bot: *tổng hợp tin nhắn hôm nay*, *tóm tắt tin nhắn nhóm kinh doanh tuần này gửi file excel*,
   hoặc gõ `/tonghop` ngay trong nhóm. Bot tóm tắt, phân loại theo nhóm công việc, liệt kê đầu việc – tiến độ – vấn đề cần xử lý.

## Sếp dùng trên Telegram

| Sếp gõ | Bot trả về |
|---|---|
| **tổng hợp tin nhắn** (hôm nay / tuần này / nhóm kinh doanh…) | 1 bản tổng hợp gồm: **1. Nội dung tin nhắn** các nhóm Telegram · **2. Tiến độ công việc** · **3. Nội dung các bài báo cáo** nhân viên đã nộp |

Dưới bản tổng hợp có nút **📗 Excel · 📘 Word · 📕 PDF**. File Excel có các sheet: Tổng hợp, Chi tiết, Tiến độ công việc,
Nội dung báo cáo, Theo nhóm công việc, Tin nhắn gốc.

Gõ `/tonghop` ngay trong nhóm Telegram = tổng hợp riêng nhóm đó.
Bot tự gửi bản tổng hợp lúc 17:30 (thứ 2 – thứ 7) và báo ngay khi có vấn đề mức **Cao**.

## Lưu ý
- Web không có đăng nhập: ai biết địa chỉ đều vào được. Chỉ nên dùng trong mạng nội bộ công ty, đừng đưa ra Internet công khai.
- Không chia sẻ file `.env` (chứa token và key).
- Bảng số liệu trong báo cáo lấy nguyên từ dữ liệu nhân viên nhập. AI chỉ viết phần tóm tắt; thiếu dữ liệu thì ghi "Chưa xác định".

## Chạy 24/7 trên Railway
1. Vào **railway.com** → đăng nhập bằng GitHub → **New Project** → **Deploy from GitHub repo** → chọn repo này.
2. Tab **Variables**: thêm `TELEGRAM_BOT_TOKEN`, `COMPANY_NAME`, `DATA_DIR=/data` (và `OPENAI_API_KEY` nếu có).
3. Chuột phải vào service → **Attach volume** → Mount path: `/data` (để dữ liệu không mất khi khởi động lại).
4. Tab **Settings → Networking → Generate Domain** → nhận link web dạng `https://xxx.up.railway.app`.
5. **Tắt `main.py` trên máy mình** – cùng một token chỉ được chạy ở 1 nơi.

## Chạy miễn phí trên Render
1. Vào **render.com** → đăng nhập bằng GitHub → **New → Blueprint** → chọn repo này → nhập `TELEGRAM_BOT_TOKEN` → **Apply**.
2. Chờ 3–5 phút, Render cho link dạng `https://eiraa-ai.onrender.com`.
3. Giữ web không ngủ: vào **uptimerobot.com** (miễn phí) → **New monitor** → HTTP(s) → URL `https://eiraa-ai.onrender.com/api/info` → 5 phút.
4. **Tắt `main.py` trên máy mình** – cùng một token chỉ chạy 1 nơi.

Lưu ý gói Free: dữ liệu bị xóa khi Render khởi động lại (hệ thống tự nạp lại dữ liệu demo).
