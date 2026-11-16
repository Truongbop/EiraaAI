"""Bot Telegram dành riêng cho sếp: hỏi gì cũng được, báo cáo + file gửi thẳng về Telegram."""
import asyncio
import logging
import re
import secrets
from pathlib import Path
from datetime import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import ai, assistant, db, exporter, files, settings, summary, tgmsg
from .config import VN_TZ

log = logging.getLogger("eiraa.bot")
APP: Application | None = None

HELP = """🤖 EIRAA AI – trợ lý báo cáo của sếp

Sếp cứ nhắn tự nhiên, ví dụ:
• tổng hợp báo cáo hôm nay
• báo cáo tuần này gửi file excel
• ai chưa nộp báo cáo
• việc nào đang quá hạn
• tình hình phòng kinh doanh tháng này
• báo cáo của Lan 7 ngày qua
• gửi file đính kèm của Hùng hôm nay

💬 Tin nhắn nhóm Telegram (thêm bot vào nhóm):
• tổng hợp tin nhắn hôm nay
• tóm tắt tin nhắn nhóm kinh doanh tuần này
• đầu việc và vấn đề trong tin nhắn hôm nay
• /tonghop [kỳ] – tổng hợp tin nhắn các nhóm

Lệnh nhanh:
/baocao [kỳ] – tổng hợp (mặc định hôm nay), vd /baocao tuần trước
/tuan  /thang – báo cáo tuần / tháng
/xuat [kỳ] – gửi file Excel + Word + PDF
/chuanop – ai chưa nộp báo cáo hôm nay
/quahan – công việc quá hạn
/congviec – công việc đang mở
/trangthai – trạng thái hệ thống"""


# ---------- Quyền: chỉ sếp mới dùng được ----------
def boss_ids():
    return {b["id"] for b in settings.bosses()}


def link_code():
    code = db.get_setting("link_code")
    if not code:
        code = secrets.token_hex(3).upper()
        db.set_setting("link_code", code)
    return code


def is_boss(update: Update):
    return bool(update.effective_user) and update.effective_user.id in boss_ids()


def _claim(user):
    settings.add_boss(user.id, user.full_name, user.username or "")
    db.log(f"tg:{user.id}", "liên kết Telegram sếp", user.full_name)


async def guard(update: Update) -> bool:
    if update.effective_chat.type != ChatType.PRIVATE:
        return False                     # bot chỉ làm việc trong chat riêng với sếp
    if is_boss(update):
        return True
    if not boss_ids():                   # chưa có sếp nào: người đầu tiên nhắn bot trở thành sếp
        _claim(update.effective_user)
        await update.effective_message.reply_text(f"✅ Đã kết nối! Chào sếp {update.effective_user.first_name}.\n\n" + HELP)
        return update.effective_message.text and not update.effective_message.text.startswith("/start")
    await update.effective_message.reply_text(
        "🔒 Bot này chỉ dành cho quản lý công ty.")
    return False


# ---------- Gửi trả lời ----------
EXPORT_KB = InlineKeyboardMarkup([[InlineKeyboardButton("📗 Excel", callback_data="x:xlsx"),
                                   InlineKeyboardButton("📘 Word", callback_data="x:docx"),
                                   InlineKeyboardButton("📕 PDF", callback_data="x:pdf"),
                                   InlineKeyboardButton("📄 TXT", callback_data="x:txt")],
                                  [InlineKeyboardButton("📦 Gửi tất cả file", callback_data="x:all")]])


async def send_reply(bot, chat_id, reply: assistant.Reply, keyboard=None):
    text = reply.text or "(không có nội dung)"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for i, ch in enumerate(chunks):
        await bot.send_message(chat_id, ch, reply_markup=keyboard if i == len(chunks) - 1 else None)
    for f in reply.files:
        path, name = (f if isinstance(f, tuple) else (f, Path(f).name))
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        with open(path, "rb") as fh:
            await bot.send_document(chat_id, fh, filename=name)


async def run_and_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE, func, *args, keyboard=None):
    chat_id = update.effective_chat.id
    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        reply = await asyncio.to_thread(func, *args)
    except Exception as e:
        log.exception("Lỗi xử lý")
        reply = assistant.Reply(f"❌ Có lỗi khi xử lý: {e}")
    await send_reply(ctx.bot, chat_id, reply, keyboard)
    return reply


def _report(period_text, formats=None):
    start, end, label = summary.parse_period(period_text)
    rep = summary.build(start, end, label, summary.find_department(period_text))
    return assistant.Reply(summary.to_message(rep), exporter.export(rep, formats) if formats else [])


# ---------- Lệnh ----------
async def cmd_start(update: Update, ctx):
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if is_boss(update):
        await update.message.reply_text(HELP)
    else:
        await guard(update)        # người đầu tiên sẽ được nhận làm sếp


async def cmd_link(update: Update, ctx):
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    code = (ctx.args[0] if ctx.args else "").strip().upper()
    if code and code == link_code():
        _claim(update.effective_user)
        db.set_setting("link_code", secrets.token_hex(3).upper())      # mã dùng 1 lần
        await update.message.reply_text(f"✅ Đã liên kết! Chào sếp {update.effective_user.first_name}.\n\n" + HELP)
    else:
        db.log(f"tg:{update.effective_user.id}", "liên kết sai mã", update.effective_user.full_name)
        await update.message.reply_text("❌ Mã liên kết không đúng hoặc đã dùng.")


async def cmd_id(update: Update, ctx):
    await update.message.reply_text(f"Telegram ID của bạn: {update.effective_user.id}")


def _period_cmd(default):
    async def handler(update: Update, ctx):
        if not await guard(update):
            return
        period = " ".join(ctx.args) or default
        ctx.user_data["last_period"] = period
        await run_and_send(update, ctx, _report, period, keyboard=EXPORT_KB)
    return handler


async def cmd_xuat(update: Update, ctx):
    if not await guard(update):
        return
    period = " ".join(ctx.args) or "hôm nay"
    ctx.user_data["last_period"] = period
    await update.message.reply_text("⏳ Em đang lập file báo cáo...")
    await run_and_send(update, ctx, _report, period, ["xlsx", "docx", "pdf"])


async def cmd_chuanop(update: Update, ctx):
    if await guard(update):
        await run_and_send(update, ctx, lambda p: assistant.Reply(assistant.tool_missing(p)), " ".join(ctx.args) or "hôm nay")


async def cmd_quahan(update: Update, ctx):
    if await guard(update):
        await run_and_send(update, ctx, lambda: assistant.Reply(assistant.tool_tasks(qua_han=True)))


async def cmd_congviec(update: Update, ctx):
    if await guard(update):
        await run_and_send(update, ctx, lambda: assistant.Reply(assistant.tool_tasks()))


async def cmd_status(update: Update, ctx):
    if not await guard(update):
        return
    d = db.dashboard(db.today())
    await update.message.reply_text(
        f"🟢 EIRAA AI đang hoạt động\nAI: {'ChatGPT (' + ai.model() + ')' if ai.enabled() else 'chế độ cơ bản – chưa nhập OpenAI key'}\n"
        f"Hôm nay: {d['submitted']}/{d['staff']} đã nộp · {d['tasks_open']} việc mở · {d['tasks_overdue']} quá hạn\n"
        f"Tự gửi tổng hợp: {settings.get('daily_time') or 'tắt'} · Nhắc chưa nộp: {settings.get('remind_time') or 'tắt'}")


# ---------- Tin nhắn tự do: hỏi gì cũng được ----------
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    q = update.message.text.strip()
    history = ctx.user_data.setdefault("history", [])
    db.log(f"tg:{update.effective_user.id}", "sếp hỏi", q)
    ql = q.lower()
    is_report = any(k in ql for k in ("tổng hợp", "báo cáo", "tóm tắt", "tình hình", "phân loại", "đầu việc")) and \
        not any(k in ql for k in ("chưa nộp", "báo cáo của", "file đính kèm", "file gốc"))
    if is_report:
        ctx.user_data["last_period"] = q
        ctx.user_data["last_kind"] = "msg" if assistant.is_message_question(ql) else "report"
    reply = await run_and_send(update, ctx, assistant.answer, q, list(history),
                               keyboard=EXPORT_KB if is_report else None)
    history += [{"role": "user", "content": q}, {"role": "assistant", "content": reply.text[:3000]}]
    del history[:-12]


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_boss(update):
        return await q.answer("Không có quyền", show_alert=True)
    await q.answer("Đang lập file...")
    fmt = q.data.split(":", 1)[1]
    formats = ["xlsx", "docx", "pdf", "txt"] if fmt == "all" else [fmt]
    period = ctx.user_data.get("last_period", "hôm nay")
    func = _msg_files_only if ctx.user_data.get("last_kind") == "msg" else _report_files_only
    await run_and_send(update, ctx, func, period, formats)


def _report_files_only(period_text, formats):
    start, end, label = summary.parse_period(period_text)
    rep = summary.build(start, end, label, summary.find_department(period_text))
    return assistant.Reply(f"📎 File báo cáo {label}:", exporter.export(rep, formats))


def _msg_files_only(period_text, formats):
    rep = tgmsg.build(period_text)
    return assistant.Reply(f"📎 File tổng hợp tin nhắn {rep['label']}:", exporter.export(rep, formats))


def _msg_report(period_text, chat_ids=None, formats=None):
    rep = tgmsg.build(period_text, chat_ids)
    return assistant.Reply(tgmsg.to_message(rep), exporter.export(rep, formats) if formats else [])


# ---------- Tin nhắn nhóm: ghi nhận ----------
async def on_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ghi lại mọi tin nhắn trong nhóm có bot (cần tắt Privacy Mode trong @BotFather)."""
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not user or user.is_bot:
        return
    tgmsg.upsert_chat(chat.id, chat.title, chat.type)
    name = user.full_name + (f" (@{user.username})" if user.username else "")
    text, kind, fname = msg.text or msg.caption or "", "text", None
    doc = msg.document
    if doc:
        kind, fname = "file", doc.file_name or "file"
        ext = Path(fname).suffix.lower()
        if ext in (".docx", ".xlsx", ".pdf", ".txt", ".csv") and (doc.file_size or 0) <= 5 * 1024 * 1024:
            try:
                f = await doc.get_file()
                data = bytes(await f.download_as_bytearray())
                _, content = await asyncio.to_thread(files.save_upload, files.safe_name(fname), data)
                text = (text + "\n" if text else "") + f"[File {fname}] " + (content or "")[:3000]
            except Exception:
                log.exception("Không đọc được file trong nhóm")
                text = (text + " " if text else "") + f"[File {fname}]"
        else:
            text = (text + " " if text else "") + f"[File {fname}]"
    elif msg.photo:
        kind, text = "photo", (text or "") + " [Ảnh]"
    if not text.strip():
        return
    tgmsg.save_message(chat.id, msg.message_id, user.id, name, text.strip(), msg.date, kind, fname)


async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Bot được thêm / bị xóa khỏi nhóm."""
    cm = update.my_chat_member
    chat = cm.chat
    if chat.type == ChatType.PRIVATE:
        return
    joined = cm.new_chat_member.status in ("member", "administrator")
    tgmsg.upsert_chat(chat.id, chat.title, chat.type, active=joined)
    db.log(f"tg:{cm.from_user.id}", "thêm bot vào nhóm" if joined else "xóa bot khỏi nhóm", chat.title)
    if joined:
        await ctx.bot.send_message(chat.id, "👋 Xin chào! Em là EIRAA AI.\nEm sẽ ghi nhận nội dung trao đổi trong nhóm để "
                                            "tổng hợp, tóm tắt và lập báo cáo công việc cho quản lý.")
        for bid in boss_ids():
            try:
                await ctx.bot.send_message(bid, f"✅ Bot đã được thêm vào nhóm “{chat.title}”. Từ giờ em ghi nhận tin nhắn nhóm này.\n"
                                                "Nhắn “tổng hợp tin nhắn hôm nay” để xem tổng hợp.")
            except Exception:
                pass


async def cmd_tonghop(update: Update, ctx):
    """/tonghop [kỳ] – chat riêng: tổng hợp mọi nhóm; trong nhóm: tổng hợp nhóm đó (chỉ sếp)."""
    chat = update.effective_chat
    period = " ".join(ctx.args) or "hôm nay"
    if chat.type == ChatType.PRIVATE:
        if not await guard(update):
            return
        ctx.user_data.update(last_period=period, last_kind="msg")
        return await run_and_send(update, ctx, _msg_report, period, keyboard=EXPORT_KB)
    if not is_boss(update):
        return await update.effective_message.reply_text("Chỉ quản lý mới dùng được lệnh này.")
    await run_and_send(update, ctx, _msg_report, period, [chat.id])


# ---------- Tự động ----------
async def notify_bosses(text, files=None, keyboard=None):
    """Gọi từ web (vd. nhân viên báo vấn đề mức CAO) để báo ngay cho sếp."""
    if not APP:
        return
    for bid in boss_ids():
        try:
            await send_reply(APP.bot, bid, assistant.Reply(text, files or []), keyboard)
        except Exception:
            log.exception("Không gửi được cho sếp %s", bid)


async def job_daily(ctx):
    reply = await asyncio.to_thread(_report, "hôm nay")
    msg_rep = await asyncio.to_thread(tgmsg.build, "hôm nay")
    for bid in boss_ids():
        await send_reply(ctx.bot, bid, assistant.Reply("⏰ Báo cáo tự động cuối ngày\n\n" + reply.text), EXPORT_KB)
        if msg_rep["messages"]:
            await send_reply(ctx.bot, bid, assistant.Reply(tgmsg.to_message(msg_rep)))


async def job_remind(ctx):
    text = await asyncio.to_thread(assistant.tool_missing, "hôm nay")
    for bid in boss_ids():
        await ctx.bot.send_message(bid, "🔔 Nhắc trước giờ tổng hợp:\n" + text)


async def on_error(update, ctx):
    log.error("Lỗi Telegram: %s", ctx.error, exc_info=ctx.error)


def build(token, request=None) -> Application:
    global APP
    b = Application.builder().token(token)
    if request:                       # dùng cho kiểm thử tự động
        b = b.request(request).get_updates_request(request)
    app = b.build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("lienket", cmd_link))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["baocao", "report", "summary", "daily"], _period_cmd("hôm nay")))
    app.add_handler(CommandHandler(["tuan", "weekly"], _period_cmd("tuần này")))
    app.add_handler(CommandHandler(["thang", "monthly"], _period_cmd("tháng này")))
    app.add_handler(CommandHandler(["xuat", "export"], cmd_xuat))
    app.add_handler(CommandHandler("chuanop", cmd_chuanop))
    app.add_handler(CommandHandler("quahan", cmd_quahan))
    app.add_handler(CommandHandler("congviec", cmd_congviec))
    app.add_handler(CommandHandler(["trangthai", "status"], cmd_status))
    app.add_handler(CommandHandler(["tonghop", "tinnhan"], cmd_tonghop))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND
                                   & (filters.TEXT | filters.CAPTION | filters.Document.ALL | filters.PHOTO), on_group_message))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^x:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    for t, job in ((settings.get("daily_time"), job_daily), (settings.get("remind_time"), job_remind)):
        if t and re.fullmatch(r"\d{1,2}:\d{2}", t):
            hh, mm = map(int, t.split(":"))
            app.job_queue.run_daily(job, time(hh, mm, tzinfo=VN_TZ), days=(1, 2, 3, 4, 5, 6))   # T2–T7
    APP = app
    return app
