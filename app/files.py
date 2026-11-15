"""Lưu file nhân viên đính kèm và trích nội dung chữ để AI đọc."""
import re
import uuid
from pathlib import Path

from .config import DATA_DIR

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED = {".xlsx", ".xls", ".docx", ".doc", ".pdf", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".pptx", ".zip"}
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 20_000


def safe_name(name):
    name = Path(name or "file").name
    return re.sub(r"[^\w.\- ()À-ỹ]", "_", name)[:120] or "file"


def save_upload(filename, data: bytes):
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"Không hỗ trợ định dạng {ext}")
    if len(data) > MAX_BYTES:
        raise ValueError("File vượt quá 20MB")
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    return path, extract_text(path)


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".csv"):
            return path.read_text(encoding="utf-8", errors="ignore")[:MAX_TEXT]
        if ext == ".docx":
            from docx import Document
            d = Document(str(path))
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for t in d.tables:
                parts += [" | ".join(c.text.strip() for c in row.cells) for row in t.rows]
            return "\n".join(parts)[:MAX_TEXT]
        if ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(str(path), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"[Sheet {ws.title}]")
                for row in ws.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        parts.append(" | ".join("" if v is None else str(v) for v in row))
                    if sum(map(len, parts)) > MAX_TEXT:
                        break
            return "\n".join(parts)[:MAX_TEXT]
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages[:30])[:MAX_TEXT]
    except Exception as e:
        return f"(Không đọc được nội dung: {e})"
    return ""   # ảnh, zip...: chỉ lưu file
