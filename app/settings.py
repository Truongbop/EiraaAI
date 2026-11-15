"""Cấu hình chỉnh được trên web (lưu trong DB). Nếu chưa nhập trên web thì lấy từ file .env."""
import json

from . import config, db

DEFAULTS = {
    "telegram_token": lambda: config.TELEGRAM_BOT_TOKEN,
    "openai_key": lambda: config.OPENAI_API_KEY,
    "openai_model": lambda: config.OPENAI_MODEL,
    "daily_time": lambda: config.DAILY_REPORT_TIME,
    "remind_time": lambda: config.REMIND_TIME,
}


def get(key):
    v = db.get_setting("cfg_" + key)
    return v if v is not None else DEFAULTS[key]()


def set(key, value):
    db.set_setting("cfg_" + key, (value or "").strip())


def mask(v):
    return (v[:6] + "…" + v[-4:]) if v and len(v) > 12 else ("đã nhập" if v else "")


# ---------- Danh sách sếp dùng bot ----------
def bosses():
    try:
        lst = json.loads(db.get_setting("bosses") or "[]")
    except ValueError:
        lst = []
    known = {b["id"] for b in lst}
    lst += [{"id": i, "name": f"ID {i} (từ .env)"} for i in config.BOSS_TELEGRAM_IDS if i not in known]
    return lst


def add_boss(uid, name, username=""):
    lst = [b for b in bosses() if b["id"] != uid]
    lst.append({"id": uid, "name": name, "username": username})
    db.set_setting("bosses", json.dumps(lst, ensure_ascii=False))


def remove_boss(uid):
    db.set_setting("bosses", json.dumps([b for b in bosses() if b["id"] != uid], ensure_ascii=False))
