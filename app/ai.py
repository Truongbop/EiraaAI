"""Kết nối OpenAI (ChatGPT). Không có key → trả None để hệ thống dùng chế độ cơ bản."""
import json
import logging

from . import settings

log = logging.getLogger("eiraa.ai")
_client = None
_client_key = None


def enabled():
    return bool(settings.get("openai_key"))


def model():
    return settings.get("openai_model") or "gpt-4o-mini"


def client():
    global _client, _client_key
    key = settings.get("openai_key")
    if not key:
        return None
    if _client is None or key != _client_key:
        from openai import OpenAI
        _client, _client_key = OpenAI(api_key=key, timeout=90), key
    return _client


def check_key(key, model_name):
    """Thử gọi OpenAI để biết key/model dùng được không."""
    from openai import OpenAI
    OpenAI(api_key=key, timeout=30).chat.completions.create(
        model=model_name, max_tokens=5, messages=[{"role": "user", "content": "ping"}])


SUMMARY_PROMPT = """Bạn là EIRAA AI, trợ lý tổng hợp báo cáo cho giám đốc. Dữ liệu dưới đây là báo cáo công việc
nhân viên tự nhập trên hệ thống. Viết tiếng Việt, ngắn gọn, đi thẳng vào điều sếp cần biết.
QUY TẮC: chỉ dùng thông tin có trong dữ liệu, KHÔNG suy diễn; thiếu thông tin thì ghi "Chưa xác định".
Trả về JSON:
{"tom_tat": "4-7 câu: tình hình chung, kết quả nổi bật, điểm đáng lo",
 "theo_phong_ban": [{"ten": "tên phòng", "tom_tat": "1-3 câu"}],
 "rui_ro": ["vấn đề / rủi ro / việc trễ cần sếp xử lý, ghi rõ ai"],
 "goi_y": ["tối đa 3 gợi ý hành động cho sếp, dựa trên dữ liệu"]}"""


def summarize(dataset_text: str):
    c = client()
    if not c:
        return None
    try:
        r = c.chat.completions.create(
            model=model(), temperature=0.2, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SUMMARY_PROMPT},
                      {"role": "user", "content": dataset_text[-100_000:]}])
        d = json.loads(r.choices[0].message.content)
        return {"tom_tat": d.get("tom_tat", ""), "theo_phong_ban": d.get("theo_phong_ban", []) or [],
                "rui_ro": d.get("rui_ro", []) or [], "goi_y": d.get("goi_y", []) or [], "ai": True}
    except Exception as e:
        log.warning("OpenAI lỗi, dùng chế độ cơ bản: %s", e)
        return None


def run_agent(system: str, history: list, tools: list, execute, max_steps=5):
    """Vòng gọi công cụ: AI tự chọn tra dữ liệu gì rồi trả lời. execute(name, args) -> str."""
    c = client()
    msgs = [{"role": "system", "content": system}] + history
    for _ in range(max_steps):
        r = c.chat.completions.create(model=model(), temperature=0.3, messages=msgs, tools=tools)
        m = r.choices[0].message
        if not m.tool_calls:
            return m.content or ""
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
        for tc in m.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                out = execute(tc.function.name, args)
            except Exception as e:
                log.exception("Công cụ %s lỗi", tc.function.name)
                out = f"Lỗi: {e}"
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": str(out)[:30_000]})
    return "Yêu cầu hơi phức tạp, sếp hỏi cụ thể hơn giúp em nhé."
