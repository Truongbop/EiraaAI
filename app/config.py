"""Cấu hình đọc từ file .env (không ghi token/key vào code)."""
import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _ids(name):
    return {int(x) for x in os.getenv(name, "").replace(" ", "").split(",") if x.lstrip("-").isdigit()}


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BOSS_TELEGRAM_IDS = _ids("BOSS_TELEGRAM_IDS")          # ID Telegram của sếp (được dùng bot)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123").strip()
SECRET_KEY = os.getenv("SECRET_KEY", "doi-chuoi-nay-khi-chay-that").encode()
COMPANY_NAME = os.getenv("COMPANY_NAME", "Công ty").strip()

DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "17:30").strip()      # gửi tổng hợp cho sếp
REMIND_TIME = os.getenv("REMIND_TIME", "16:30").strip()                  # báo sếp ai chưa nộp
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "eiraa.db"
EXPORT_DIR = DATA_DIR / "exports"
LOG_DIR = BASE_DIR / "logs"
for _d in (DATA_DIR, EXPORT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

VN_TZ = timezone(timedelta(hours=7))
