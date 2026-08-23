"""Token estimation, window trimming, context compression."""
from . import api


def estimate_tokens(parts: list) -> int:
    """粗略估算：中文约 0.6 token/字，英文约 0.3；图片按 1100。"""
    n = 0
    for p in parts:
        if p.get("type") == "text":
            t = p["text"]
            cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
            n += int(cjk * 0.6 + (len(t) - cjk) * 0.3)
        elif p.get("type") == "image_url":
            n += 1100
    return max(n, 1)


def estimate_messages(msgs: list[api.Msg]) -> int:
    return sum(estimate_tokens(m.parts) for m in msgs)


SUMMARY_SYSTEM = "把以下对话压缩成简明中文摘要，保留：主题、关键结论、未解决问题、用户偏好。直接输出摘要，不要多余说明。"


async def compress(msgs: list[api.Msg], cfg: dict, model: str) -> str:
    """调用模型生成摘要；失败返回空串。"""
    conv = [{"role": m.role, "content": "".join(p.get("text", "") for p in m.parts if p.get("type") == "text")} for m in msgs]
    payload = [{"role": "system", "content": SUMMARY_SYSTEM}] + conv
    text = ""
    async for block in api.stream_chat(cfg, model, payload, thinking="off"):
        if block.error:
            return ""
        text = block.text
    return text.strip()


async def prepare(msgs: list[api.Msg], system_prompt: str, cfg: dict, model: str,
                  window: int, threshold: float, keep_last: int, reserve: int = 0,
                  max_input: int = 0) -> tuple[list[dict], dict]:
    """组装请求体并保证不超窗。返回 (api_messages, stats)，压缩产物由调用方落库。"""
    stats = {"estimated": 0, "compressed": 0, "dropped": 0, "summaries": [], "summary_text": ""}
    work = list(msgs)
    budget = max(1, window - reserve)  # 输入预算：窗口扣除输出预留
    if max_input:
        budget = min(budget, max_input)
    limit = int(budget * threshold)

    # 循环压缩最早的可压缩区间，直到低于阈值
    while True:
        total = estimate_messages(work)
        stats["estimated"] = total
        if total <= limit:
            break
        start = 0
        while start < len(work) and (work[start].summary or work[start].role == "system"):
            start += 1
        protect = max(0, len(work) - keep_last)
        if start >= protect:
            break
        batch = work[start:min(protect, start + 50)]
        text = await compress(batch, cfg, model)
        if not text:
            break  # 压缩失败，回退到紧急截断
        summary_msg = api.Msg(role="system", parts=[{"type": "text", "text": "[早期对话摘要] " + text}], summary=True)
        work = work[:start] + [summary_msg] + work[protect:]
        stats["compressed"] += len(batch)
        stats["summary_text"] = text
        stats["summaries"].append((batch[0].id, text, [m.id for m in batch if m.id is not None]))

    # 仍然超窗（system/图片过大等）-> 紧急截断最老的非 system 消息
    while estimate_messages(work) > budget and any(m.role != "system" for m in work):
        for i, m in enumerate(work):
            if m.role != "system":
                work.pop(i)
                stats["dropped"] += 1
                break
    stats["estimated"] = estimate_messages(work)
    return api.to_api_messages(work, system_prompt), stats
