"""Cơ sở dữ liệu SQLite: nhân viên, báo cáo ngày, công việc, nhật ký."""
import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from .config import ADMIN_PASSWORD, ADMIN_USERNAME, DB_PATH, VN_TZ

STATUSES = ["Chưa làm", "Đang làm", "Hoàn thành", "Tạm dừng"]
LEVELS = ["Thấp", "Trung bình", "Cao"]


def now():
    return datetime.now(VN_TZ)


def today():
    return now().date()


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


# ---------- Mật khẩu ----------
def hash_pw(pw, salt=None):
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${h}"


def check_pw(pw, stored):
    try:
        salt, _ = stored.split("$", 1)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hash_pw(pw, salt), stored)


def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
            department TEXT DEFAULT '', position TEXT DEFAULT '',
            role TEXT DEFAULT 'staff',          -- admin | staff
            pw_hash TEXT NOT NULL, active INTEGER DEFAULT 1, created_at TEXT);

        CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_date TEXT NOT NULL,
            done TEXT DEFAULT '', progress INTEGER DEFAULT 0,
            plan TEXT DEFAULT '', issues TEXT DEFAULT '', issue_level TEXT DEFAULT '',
            proposals TEXT DEFAULT '', created_at TEXT, updated_at TEXT,
            UNIQUE(user_id, report_date));

        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, description TEXT DEFAULT '',
            assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            deadline TEXT DEFAULT '', status TEXT DEFAULT 'Chưa làm', progress INTEGER DEFAULT 0,
            created_by INTEGER, created_at TEXT, updated_at TEXT);

        CREATE TABLE IF NOT EXISTS task_updates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, user_id INTEGER,
            status TEXT, progress INTEGER, note TEXT, created_at TEXT);

        CREATE TABLE IF NOT EXISTS attachments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
            filename TEXT, stored_path TEXT, size INTEGER, content TEXT, created_at TEXT);

        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, detail TEXT, created_at TEXT);
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(attachments)")]
        if "subject" not in cols:
            c.execute("ALTER TABLE attachments ADD COLUMN subject TEXT DEFAULT ''")
        if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            c.execute("INSERT INTO users(username,full_name,role,pw_hash,created_at) VALUES(?,?,?,?,?)",
                      (ADMIN_USERNAME, "Quản trị viên", "admin", hash_pw(ADMIN_PASSWORD), now().isoformat()))


def log(actor, action, detail=""):
    with conn() as c:
        c.execute("INSERT INTO audit(actor,action,detail,created_at) VALUES(?,?,?,?)",
                  (str(actor), action, str(detail)[:500], now().isoformat()))


def get_setting(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))


# ---------- Nhân viên ----------
USER_COLS = "id,username,full_name,department,position,role,active,created_at"


def user_by_username(username):
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(r) if r else None


def user_by_id(uid):
    with conn() as c:
        r = c.execute(f"SELECT {USER_COLS} FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def list_users(active_only=False):
    with conn() as c:
        return rows(c, f"SELECT {USER_COLS} FROM users {'WHERE active=1' if active_only else ''} ORDER BY department, full_name")


def create_user(username, full_name, password, department="", position="", role="staff"):
    with conn() as c:
        c.execute("INSERT INTO users(username,full_name,department,position,role,pw_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                  (username, full_name, department, position, role, hash_pw(password), now().isoformat()))


def create_staff(full_name, department="", position=""):
    """Thêm nhân viên (web dùng chung, không đăng nhập nên không cần mật khẩu)."""
    username = "nv_" + os.urandom(4).hex()
    create_user(username, full_name, os.urandom(16).hex(), department, position)


def update_user(uid, **f):
    if f.get("password"):
        f["pw_hash"] = hash_pw(f.pop("password"))
    f.pop("password", None)
    f = {k: v for k, v in f.items() if k in ("full_name", "department", "position", "role", "active", "pw_hash") and v is not None}
    if f:
        with conn() as c:
            c.execute(f"UPDATE users SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), uid))


def departments():
    with conn() as c:
        return [r["department"] for r in c.execute(
            "SELECT DISTINCT department FROM users WHERE active=1 AND department!='' ORDER BY department")]


# ---------- Báo cáo ngày ----------
def upsert_report(user_id, report_date, done, progress, plan, issues, issue_level, proposals):
    ts = now().isoformat()
    with conn() as c:
        c.execute("""INSERT INTO reports(user_id,report_date,done,progress,plan,issues,issue_level,proposals,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(user_id,report_date) DO UPDATE SET done=excluded.done, progress=excluded.progress,
                       plan=excluded.plan, issues=excluded.issues, issue_level=excluded.issue_level,
                       proposals=excluded.proposals, updated_at=excluded.updated_at""",
                  (user_id, report_date, done, progress, plan, issues, issue_level, proposals, ts, ts))


def get_report(user_id, report_date):
    with conn() as c:
        r = c.execute("SELECT * FROM reports WHERE user_id=? AND report_date=?", (user_id, report_date)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["attachments"] = [{k: v for k, v in a.items() if k != "content"} for a in attachments_for([d["id"]]).get(d["id"], [])]
    return d


def list_reports(start: date, end: date, user_id=None, department=None):
    sql = """SELECT r.*, u.full_name, u.department, u.position FROM reports r JOIN users u ON u.id=r.user_id
             WHERE r.report_date BETWEEN ? AND ?"""
    args = [start.isoformat(), end.isoformat()]
    if user_id:
        sql += " AND r.user_id=?"
        args.append(user_id)
    if department:
        sql += " AND u.department=?"
        args.append(department)
    sql += " ORDER BY r.report_date DESC, u.department, u.full_name"
    with conn() as c:
        result = rows(c, sql, args)
    att = attachments_for([r["id"] for r in result])
    for r in result:
        r["attachments"] = att.get(r["id"], [])
    return result


def attach(report_id, filename, stored_path, size, content, subject=""):
    with conn() as c:
        c.execute("INSERT INTO attachments(report_id,filename,stored_path,size,content,created_at,subject) VALUES(?,?,?,?,?,?,?)",
                  (report_id, filename, stored_path, size, content, now().isoformat(), subject))


def submit_file(user_id, subject, filename, stored_path, size, content, report_date=None):
    """Nộp 1 file báo cáo: tạo/cập nhật báo cáo ngày của nhân viên và gắn file (kèm chủ đề) vào đó."""
    d = (report_date or today()).isoformat()
    rep = get_report(user_id, d)
    if rep:
        lines = [x for x in rep["done"].splitlines() if x.strip()]
        if subject not in lines:
            lines.append(subject)
        upsert_report(user_id, d, "\n".join(lines), rep["progress"], rep["plan"], rep["issues"],
                      rep["issue_level"], rep["proposals"])
    else:
        upsert_report(user_id, d, subject, 0, "", "", "", "")
    rep = get_report(user_id, d)
    attach(rep["id"], filename, stored_path, size, content, subject)


def list_submissions(start: date, end: date, user_id=None):
    sql = """SELECT a.id, a.filename, a.size, a.subject, a.content, a.created_at, r.report_date,
                    u.id AS user_id, u.full_name, u.department
             FROM attachments a JOIN reports r ON r.id=a.report_id JOIN users u ON u.id=r.user_id
             WHERE r.report_date BETWEEN ? AND ?"""
    args = [start.isoformat(), end.isoformat()]
    if user_id:
        sql += " AND u.id=?"
        args.append(user_id)
    with conn() as c:
        return rows(c, sql + " ORDER BY a.id DESC", args)


def attachments_for(report_ids):
    if not report_ids:
        return {}
    q = ",".join("?" * len(report_ids))
    with conn() as c:
        out = {}
        for r in rows(c, f"SELECT * FROM attachments WHERE report_id IN ({q}) ORDER BY id", list(report_ids)):
            out.setdefault(r["report_id"], []).append(r)
        return out


def get_attachment(aid):
    with conn() as c:
        r = c.execute("SELECT a.*, r.user_id FROM attachments a JOIN reports r ON r.id=a.report_id WHERE a.id=?", (aid,)).fetchone()
    return dict(r) if r else None


def delete_attachment(aid):
    with conn() as c:
        c.execute("DELETE FROM attachments WHERE id=?", (aid,))


def missing_reports(d: date, department=None):
    sql = """SELECT id,full_name,department,position FROM users
             WHERE active=1 AND role='staff' AND id NOT IN (SELECT user_id FROM reports WHERE report_date=?)"""
    args = [d.isoformat()]
    if department:
        sql += " AND department=?"
        args.append(department)
    with conn() as c:
        return rows(c, sql + " ORDER BY department, full_name", args)


# ---------- Công việc ----------
def list_tasks(assignee_id=None, status=None, department=None, overdue=False):
    sql = """SELECT t.*, u.full_name AS assignee, u.department FROM tasks t
             LEFT JOIN users u ON u.id=t.assignee_id WHERE 1=1"""
    args = []
    if assignee_id:
        sql += " AND t.assignee_id=?"
        args.append(assignee_id)
    if status:
        sql += " AND t.status=?"
        args.append(status)
    if department:
        sql += " AND u.department=?"
        args.append(department)
    if overdue:
        sql += " AND t.status!='Hoàn thành' AND t.deadline!='' AND t.deadline<?"
        args.append(today().isoformat())
    sql += " ORDER BY CASE t.status WHEN 'Hoàn thành' THEN 1 ELSE 0 END, t.deadline='' , t.deadline"
    with conn() as c:
        return rows(c, sql, args)


def get_task(tid):
    with conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    return dict(r) if r else None


def create_task(title, description, assignee_id, deadline, created_by):
    ts = now().isoformat()
    with conn() as c:
        cur = c.execute("""INSERT INTO tasks(title,description,assignee_id,deadline,created_by,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?)""", (title, description, assignee_id, deadline, created_by, ts, ts))
        return cur.lastrowid


def update_task(tid, user_id, **f):
    f = {k: v for k, v in f.items() if k in ("title", "description", "assignee_id", "deadline", "status", "progress") and v is not None}
    if f.get("status") == "Hoàn thành":
        f["progress"] = 100
    if not f:
        return
    ts = now().isoformat()
    with conn() as c:
        c.execute(f"UPDATE tasks SET {','.join(k + '=?' for k in f)}, updated_at=? WHERE id=?", (*f.values(), ts, tid))


def add_task_update(tid, user_id, status, progress, note):
    with conn() as c:
        c.execute("INSERT INTO task_updates(task_id,user_id,status,progress,note,created_at) VALUES(?,?,?,?,?,?)",
                  (tid, user_id, status, progress, note, now().isoformat()))


def task_updates_between(start: date, end: date):
    with conn() as c:
        return rows(c, """SELECT tu.*, t.title, u.full_name FROM task_updates tu
                          JOIN tasks t ON t.id=tu.task_id LEFT JOIN users u ON u.id=tu.user_id
                          WHERE substr(tu.created_at,1,10) BETWEEN ? AND ? ORDER BY tu.created_at""",
                    (start.isoformat(), end.isoformat()))


def delete_task(tid):
    with conn() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))


def dashboard(d: date):
    with conn() as c:
        one = lambda q, a=(): c.execute(q, a).fetchone()[0]
        return {
            "staff": one("SELECT COUNT(*) FROM users WHERE active=1 AND role='staff'"),
            "submitted": one("SELECT COUNT(*) FROM reports r JOIN users u ON u.id=r.user_id WHERE report_date=? AND u.role='staff'", (d.isoformat(),)),
            "tasks_open": one("SELECT COUNT(*) FROM tasks WHERE status!='Hoàn thành'"),
            "tasks_done": one("SELECT COUNT(*) FROM tasks WHERE status='Hoàn thành'"),
            "tasks_overdue": one("SELECT COUNT(*) FROM tasks WHERE status!='Hoàn thành' AND deadline!='' AND deadline<?", (d.isoformat(),)),
            "issues_high": one("SELECT COUNT(*) FROM reports WHERE report_date=? AND issue_level='Cao'", (d.isoformat(),)),
        }
