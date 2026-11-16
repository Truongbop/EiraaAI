"""EIRAA AI – chạy 1 lệnh: web báo cáo (dùng chung, không đăng nhập) + bot Telegram cho sếp.

    python main.py      → web: http://localhost:8000
"""
import asyncio
import logging
import os
import subprocess
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, files, settings, tgmsg
from app import telegram_bot as tgbot
from app.config import BASE_DIR, COMPANY_NAME, HOST, LOG_DIR, PORT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(),
                              RotatingFileHandler(LOG_DIR / "eiraa.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("eiraa")
BOT = {"app": None, "username": None, "error": None}


# ================= Bot Telegram chạy cùng web =================
async def start_bot():
    token = settings.get("telegram_token")
    if not token:
        BOT["error"] = "Chưa có TELEGRAM_BOT_TOKEN trong file .env"
        log.warning(BOT["error"])
        return
    try:
        app = tgbot.build(token)
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=["message", "callback_query", "my_chat_member"], drop_pending_updates=True)
        BOT.update(app=app, username=app.bot.username, error=None)
        log.info("Bot Telegram @%s đang chạy", app.bot.username)
    except Exception as e:
        BOT["error"] = f"Không kết nối được Telegram: {e}"
        log.exception("Không khởi động được bot Telegram")


async def stop_bot():
    app = BOT["app"]
    if app:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def open_chrome(url):
    """Mở trang web bằng Chrome nếu có, không thì dùng trình duyệt mặc định."""
    candidates = [Path(os.environ.get(v, ""), "Google", "Chrome", "Application", "chrome.exe")
                  for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for exe in candidates:
        if exe.is_file():
            try:
                subprocess.Popen([str(exe), url])
                return
            except OSError:
                pass
    webbrowser.open(url)


@asynccontextmanager
async def lifespan(_):
    db.init()
    db.set_setting("company", COMPANY_NAME)
    if os.getenv("DEMO_DATA", "1") != "0":
        from app import demo
        await asyncio.to_thread(demo.seed_if_empty)       # lần đầu chạy, chưa có nhân viên → nạp dữ liệu demo
        await asyncio.to_thread(demo.seed_tg_if_empty)    # chưa có tin nhắn nhóm → nạp tin nhắn demo
    await start_bot()
    url = f"http://localhost:{PORT}"
    log.info("Web đang chạy: %s  (giữ cửa sổ này mở, bấm Ctrl+C để tắt)", url)
    if os.name == "nt" and os.getenv("OPEN_BROWSER", "1") != "0":      # chỉ tự mở Chrome khi chạy trên Windows
        asyncio.get_running_loop().call_later(1.0, open_chrome, url)
    yield
    await stop_bot()


api = FastAPI(title="EIRAA AI", lifespan=lifespan)


def _user(uid):
    u = db.user_by_id(uid) if uid else None
    if not u or not u["active"]:
        raise HTTPException(400, "Hãy chọn nhân viên")
    return u


@api.get("/api/info")
def info():
    return {"company": COMPANY_NAME, "today": db.today().isoformat(), "bot": BOT["username"], "bot_error": BOT["error"]}


# ================= Báo cáo ngày =================
class ReportIn(BaseModel):
    user_id: int
    report_date: date | None = None
    done: str = Field(min_length=3)
    progress: int = Field(ge=0, le=100, default=0)
    plan: str = ""
    issues: str = ""
    issue_level: str = ""
    proposals: str = ""


def _check_date(d: date):
    if d > db.today():
        raise HTTPException(400, "Không thể báo cáo cho ngày tương lai")


@api.get("/api/report")
def get_report(user_id: int, d: date | None = None):
    return db.get_report(_user(user_id)["id"], (d or db.today()).isoformat())


@api.post("/api/report")
async def save_report(body: ReportIn):
    u = _user(body.user_id)
    d = body.report_date or db.today()
    _check_date(d)
    level = body.issue_level if body.issue_level in db.LEVELS else ("Trung bình" if body.issues.strip() else "")
    existed = db.get_report(u["id"], d.isoformat())
    db.upsert_report(u["id"], d.isoformat(), body.done.strip(), body.progress, body.plan.strip(),
                     body.issues.strip(), level, body.proposals.strip())
    db.log(u["full_name"], "nộp báo cáo" if not existed else "sửa báo cáo", d.isoformat())
    if level == "Cao" and body.issues.strip() and not (existed and existed["issue_level"] == "Cao"):
        asyncio.create_task(tgbot.notify_bosses(
            f"🚨 VẤN ĐỀ MỨC CAO\n👤 {u['full_name']} ({u['department'] or 'Chưa rõ phòng'})\n📅 {d:%d/%m/%Y}\n\n{body.issues.strip()}"
            + (f"\n\n💡 Đề xuất: {body.proposals.strip()}" if body.proposals.strip() else "")))
    return db.get_report(u["id"], d.isoformat())


@api.post("/api/report/attachments")
async def upload(user_id: int, file: UploadFile = File(...), d: date | None = None):
    u = _user(user_id)
    d = d or db.today()
    rep = db.get_report(u["id"], d.isoformat())
    if not rep:
        raise HTTPException(400, "Hãy lưu báo cáo trước khi đính kèm file")
    data = await file.read()
    try:
        path, text = await asyncio.to_thread(files.save_upload, files.safe_name(file.filename), data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.attach(rep["id"], files.safe_name(file.filename), str(path), len(data), text)
    db.log(u["full_name"], "đính kèm file", file.filename)
    return db.get_report(u["id"], d.isoformat())


REPORT_EXT = {".xlsx", ".xls", ".docx", ".doc"}


@api.post("/api/submit")
async def submit(user_id: int = Form(...), subject: str = Form(...), files_in: list[UploadFile] = File(..., alias="files")):
    """Nộp báo cáo bằng file Excel / Word kèm chủ đề."""
    u = _user(user_id)
    subject = subject.strip()
    if len(subject) < 2:
        raise HTTPException(400, "Hãy nhập chủ đề báo cáo")
    if not files_in:
        raise HTTPException(400, "Hãy chọn file Excel hoặc Word")
    for f in files_in:
        if Path(f.filename or "").suffix.lower() not in REPORT_EXT:
            raise HTTPException(400, f"{f.filename}: chỉ nhận file Excel (.xlsx, .xls) hoặc Word (.docx, .doc)")
    saved = []
    for f in files_in:
        name = files.safe_name(f.filename)
        data = await f.read()
        try:
            path, text = await asyncio.to_thread(files.save_upload, name, data)
        except ValueError as e:
            raise HTTPException(400, f"{name}: {e}")
        db.submit_file(u["id"], subject, name, str(path), len(data), text)
        saved.append(name)
    db.log(u["full_name"], "nộp báo cáo", f"{subject}: {', '.join(saved)}")
    return {"ok": True, "files": saved}


@api.get("/api/submissions")
def submissions(days: int = 7, user_id: int | None = None):
    out = db.list_submissions(db.today() - timedelta(days=days - 1), db.today(), user_id)
    for a in out:
        a["preview"] = (a.pop("content") or "")[:400]
    return out


@api.get("/api/attachments/{aid}")
def download_attachment(aid: int):
    a = db.get_attachment(aid)
    if not a:
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(a["stored_path"], filename=a["filename"])


@api.delete("/api/attachments/{aid}")
def remove_attachment(aid: int):
    a = db.get_attachment(aid)
    if not a:
        raise HTTPException(404, "Không tìm thấy file")
    db.delete_attachment(aid)
    Path(a["stored_path"]).unlink(missing_ok=True)
    return {"ok": True}


@api.get("/api/reports")
def reports(user_id: int | None = None, days: int = 60):
    return db.list_reports(db.today() - timedelta(days=days), db.today(), user_id=user_id)


@api.get("/api/overview")
def overview(d: date | None = None):
    d = d or db.today()
    reps = db.list_reports(d, d)
    for r in reps:
        for a in r["attachments"]:
            a.pop("content", None)
    return {"date": d.isoformat(), "reports": reps, "missing": db.missing_reports(d), "stats": db.dashboard(d)}


# ================= Công việc =================
class TaskIn(BaseModel):
    title: str = Field(min_length=2)
    description: str = ""
    assignee_id: int | None = None
    deadline: str = ""


class TaskEditIn(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee_id: int | None = None
    deadline: str | None = None
    status: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    note: str = ""


@api.get("/api/tasks")
def tasks(user_id: int | None = None):
    return db.list_tasks(assignee_id=user_id)


@api.post("/api/tasks")
def create_task(body: TaskIn):
    tid = db.create_task(body.title.strip(), body.description.strip(), body.assignee_id, body.deadline, None)
    db.log("web", "giao việc", body.title)
    return db.get_task(tid)


@api.patch("/api/tasks/{tid}")
def edit_task(tid: int, body: TaskEditIn):
    t = db.get_task(tid)
    if not t:
        raise HTTPException(404, "Không tìm thấy công việc")
    if body.status and body.status not in db.STATUSES:
        raise HTTPException(400, "Trạng thái không hợp lệ")
    db.update_task(tid, None, **body.model_dump(exclude={"note"}))
    nt = db.get_task(tid)
    if body.status is not None or body.progress is not None:
        db.add_task_update(tid, nt["assignee_id"], nt["status"], nt["progress"], body.note.strip())
        db.log("web", "cập nhật công việc", f"{nt['title']} → {nt['status']} {nt['progress']}%")
    return nt


@api.delete("/api/tasks/{tid}")
def del_task(tid: int):
    db.delete_task(tid)
    db.log("web", "xóa công việc", tid)
    return {"ok": True}


# ================= Khung chat trên web (đồng nghiệp + khách hàng) =================
class ChatIn(BaseModel):
    chat_id: int
    user_id: int
    text: str = Field(min_length=1, max_length=4000)


class CustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    company: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=20)
    text: str = Field(min_length=1, max_length=4000)
    user_id: int | None = None          # nhân viên ghi nhận thay khách (gọi điện, Zalo...)


@api.get("/api/chat/threads")
def chat_threads():
    return tgmsg.list_threads()


@api.get("/api/chat/messages")
def chat_messages(chat_id: int):
    return tgmsg.thread_messages(chat_id)


@api.post("/api/chat/send")
def chat_send(body: ChatIn):
    u = _user(body.user_id)
    try:
        tgmsg.post_web(body.chat_id, u["full_name"], body.text, u["id"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@api.post("/api/chat/customer")
def chat_new_customer(body: CustomerIn):
    """Nhân viên tạo hội thoại khách hàng mới (ghi lại tin khách gửi qua điện thoại / Zalo...)."""
    cid = tgmsg.customer_thread(body.name, body.company, body.phone)
    tgmsg.post_web(cid, body.name.strip(), body.text, None)
    if body.user_id:
        db.log(_user(body.user_id)["full_name"], "ghi nhận tin khách hàng", body.name)
    return {"ok": True, "chat_id": cid}


@api.post("/api/public/contact")
def public_contact(body: CustomerIn):
    """Trang liên hệ cho khách hàng (/lienhe.html) – khách tự gửi tin nhắn."""
    cid = tgmsg.customer_thread(body.name, body.company, body.phone)
    tgmsg.post_web(cid, body.name.strip(), body.text, None)
    return {"ok": True}


# ================= Nhân viên =================
class UserIn(BaseModel):
    full_name: str = Field(min_length=2)
    department: str = ""
    position: str = ""


class UserEditIn(BaseModel):
    full_name: str | None = None
    department: str | None = None
    position: str | None = None
    active: int | None = None


@api.get("/api/users")
def users(all: bool = False):
    return [u for u in db.list_users(active_only=not all) if u["role"] == "staff"]


@api.post("/api/users")
def add_user(body: UserIn):
    db.create_staff(body.full_name.strip(), body.department.strip(), body.position.strip())
    db.log("web", "thêm nhân viên", body.full_name)
    return {"ok": True}


@api.patch("/api/users/{uid}")
def edit_user(uid: int, body: UserEditIn):
    db.update_user(uid, **body.model_dump())
    return {"ok": True}


@api.get("/api/audit")
def audit():
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM audit ORDER BY id DESC LIMIT 100")


@api.middleware("http")
async def no_cache_web(request, call_next):
    """Luôn tải bản web mới nhất (tránh trình duyệt giữ file JS/CSS cũ sau khi cập nhật)."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


api.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(api, host=HOST, port=PORT, log_level="warning")
