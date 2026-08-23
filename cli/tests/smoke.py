"""冒烟测试：本地 mock OpenAI 兼容服务器 + 端到端验证（不依赖真实 API）。"""
import asyncio
import io
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_harness import api, ctx  # noqa: E402
from llm_harness.app import App  # noqa: E402
from llm_harness.db import DB  # noqa: E402
from rich.console import Console  # noqa: E402

ASSISTANT_TEXT = "你好！这是流式回复。"
SUMMARY_TEXT = "摘要：早期对话讨论了测试。"
FAILED = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    @staticmethod
    def _msg_text(m):
        c = m.get("content", "")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
        return ""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        msgs = body.get("messages", [])
        user_text = next((self._msg_text(m) for m in msgs if m.get("role") == "user"), "")
        if "五轮搜索" in user_text:
            self.server.search_req = getattr(self.server, "search_req", 0) + 1
            searches = ["[SEARCH: 第一组关键词]", "[SEARCH: 第二组关键词]", "[SEARCH: 第三组关键词]", "[SEARCH: 第四组关键词]", "[SEARCH: 第五组关键词]"]
            text = searches[self.server.search_req - 1] if self.server.search_req <= 5 else "这是最终直接回答。"
        elif "压缩" in self._msg_text(msgs[0]):
            text = SUMMARY_TEXT
        else:
            text = ASSISTANT_TEXT
        for ch in text:
            chunk = {"choices": [{"delta": {"content": ch}}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: {bad json\n\n")
        self.wfile.write(("data: " + json.dumps({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    tmp = Path(tempfile.mkdtemp())
    cfg = {"defaults": {"model": "mock", "context_window": 400, "compress_threshold": 0.75, "compress_keep_last": 5},
           "providers": [{"name": "mock", "base_url": f"http://127.0.0.1:{port}/v1", "api_key_env": "", "models": ["mock"]}]}
    db = DB(tmp / "test.db")
    console = Console(file=io.StringIO())

    # 1) 流式解析
    async def t1():
        out = []
        async for b in api.stream_chat(cfg["providers"][0], "mock", [{"role": "user", "content": "hi"}], "medium"):
            out.append(b)
        last = out[-1]
        return last.text == ASSISTANT_TEXT and last.usage and last.usage["total_tokens"] == 15 and all(ASSISTANT_TEXT.startswith(b.text) for b in out)
    check("api.stream_chat 流式累积 + usage", asyncio.run(t1()))

    # 2) 思考档位 -> 参数
    check("thinking_params high -> reasoning_effort",
          api.thinking_params(cfg["providers"][0], "mock", "high") == {"reasoning_effort": "high"})

    # 3) DB 基本操作
    cid = db.new_conversation(provider="mock", model="mock")
    db.add_message(cid, "user", [{"type": "text", "text": "a"}])
    db.add_message(cid, "assistant", [{"type": "text", "text": "b"}])
    check("db 消息读写", len(db.get_messages(cid)) == 2)
    db.mark_hidden(cid, [1])
    check("db hidden 过滤", len(db.get_messages(cid)) == 1 and len(db.get_messages(cid, include_hidden=True)) == 2)

    # 4) App.send 端到端（user/assistant 落库 + 标题）
    app = App(cfg, db, console=console)
    app.load_session(cid)
    asyncio.run(app.send("你好", []))
    rows = db.get_messages(cid)
    check("send 落库 user+assistant", [r["role"] for r in rows[-2:]] == ["user", "assistant"])
    check("send 自动标题", db.get_conversation(cid)["title"] == "你好")
    check("send 元信息同步", db.get_conversation(cid)["model"] == "mock")

    # 5) 上下文压缩（小窗口强制触发）
    cid2 = db.new_conversation(provider="mock", model="mock")
    long_text = "这是一个很长的测试消息。" * 30
    for i in range(10):
        db.add_message(cid2, "user", [{"type": "text", "text": long_text}])
        db.add_message(cid2, "assistant", [{"type": "text", "text": long_text}])
    msgs = [api.Msg(role=r["role"], parts=json.loads(r["content"]), id=r["id"], summary=bool(r["summary"]))
            for r in db.get_messages(cid2)]
    send, stats = asyncio.run(ctx.prepare(msgs, "sys", cfg["providers"][0], "mock", 400, 0.75, 5))
    check("压缩触发且生成摘要", stats["compressed"] > 0 and stats["summaries"])
    check("压缩后发送体含摘要", any("早期对话摘要" in "".join(p.get("text", "") for p in m["content"]) for m in send if isinstance(m["content"], list)))

    # 6.5) prepare max_input 预算
    send2, stats2 = asyncio.run(ctx.prepare(msgs, "sys", cfg["providers"][0], "mock", 400, 0.75, 5, reserve=100, max_input=150))
    check("prepare max_input 预算", stats2["estimated"] <= 150)

    # 7) fork / load
    app2 = App(cfg, db, console=console)
    app2.load_session(cid2)
    app2.cmd_fork([])
    fork_id = app2.conv["id"]
    check("fork 复制消息", len(db.get_messages(fork_id, include_hidden=True)) == len(db.get_messages(cid2, include_hidden=True)))
    app2.cmd_switch(["1"])
    before = len(db.get_messages(1))
    app2.cmd_load([str(fork_id), "2"])
    check("load 直接切换会话", app2.conv["id"] == fork_id and len(db.get_messages(1)) == before)

    # 7) settings: TOML 往返 + api_key 优先级 + 交互向导
    import tomllib
    from llm_harness import settings as st
    from unittest import mock
    test_cfg = {"defaults": {"model": "m1", "thinking": "medium"},
                "providers": [{"name": "p1", "base_url": "http://x/v1", "api_key": "sk-123", "api_key_env": "", "models": ["m1", "m2"]}]}
    check("settings TOML 往返", tomllib.loads(st.dump_toml(test_cfg)) == test_cfg)
    check("api_key 优先配置内明文", api.api_key({"api_key": "sk-file", "api_key_env": "LLH_NOPE"}) == "sk-file")

    cfg_path = tmp / "settings_test.toml"
    answers = iter(["myprov", "http://example.com/v1", "sk-new", "a1, a2", "n", "", "65536", "10000", "2048", "firecrawl", "", "", ""])
    with mock.patch("builtins.input", lambda *a: next(answers)):
        st.main(["--config", str(cfg_path)])
    p0 = tomllib.loads(cfg_path.read_text(encoding="utf-8"))["providers"][0]
    check("settings 向导写入", p0["name"] == "myprov" and p0["base_url"] == "http://example.com/v1"
          and p0["api_key"] == "sk-new" and p0["models"] == ["a1", "a2"]
          and tomllib.loads(cfg_path.read_text(encoding="utf-8"))["defaults"]["max_tokens"] == 2048
          and tomllib.loads(cfg_path.read_text(encoding="utf-8"))["defaults"]["max_input"] == 10000
          and tomllib.loads(cfg_path.read_text(encoding="utf-8"))["defaults"]["context_window"] == 65536)

    # 8) 启动会话策略：优先复用现有空会话（untitled），否则新建
    db3 = DB(tmp / "startup.db")
    app3 = App(cfg, db3, console=console)
    app3.load_session(new=True)
    first_id = app3.conv["id"]
    check("启动无空会话时新建", app3.conv["title"] == "untitled" and not db3.get_messages(first_id))
    app3.load_session(new=True)
    check("启动复用现有空会话", app3.conv["id"] == first_id)
    db3.add_message(first_id, "user", [{"type": "text", "text": "x"}])
    app3.load_session(new=True)
    check("有消息的 untitled 不再复用", app3.conv["id"] != first_id)
    db3.close()

    # 9) 搜索资料不足时可用不同关键词补搜，最多 5 轮后直接作答
    cfg9 = {"defaults": {"model": "mock", "context_window": 8192}, "providers": cfg["providers"]}
    db4 = DB(tmp / "retry.db")
    app4 = App(cfg9, db4, console=console)
    app4.load_session(new=True)
    fake_results = [{"title": "测试标题", "body": "测试正文", "href": "http://example.com"}]
    with mock.patch("llm_harness.web.web_search", return_value=fake_results) as search:
        asyncio.run(app4.send("五轮搜索测试", []))
    last_text = "".join(p.get("text", "") for p in json.loads(db4.get_messages(app4.conv["id"])[-1]["content"]))
    check("最多执行五轮不同关键词搜索", search.call_count == 5)
    check("五轮搜索后直接作答", "这是最终直接回答" in last_text)
    db4.close()

    # 10) /load 续写：新消息实时写回目标会话，退出时删除临时会话
    cfg10 = {"defaults": {"model": "mock", "context_window": 8192}, "providers": cfg["providers"]}
    db5 = DB(tmp / "writeback.db")
    app5 = App(cfg10, db5, console=console)
    target_id = db5.new_conversation(title="旧会话", provider="mock", model="mock")
    db5.add_message(target_id, "user", [{"type": "text", "text": "旧问题"}])
    db5.add_message(target_id, "assistant", [{"type": "text", "text": "旧回答"}])
    app5.load_session(new=True)
    app5.cmd_load([str(target_id)])
    check("load 直接切到目标会话", app5.conv["id"] == target_id)
    asyncio.run(app5.send("新消息", []))
    target_rows = db5.get_messages(target_id)
    check("聊天消息直接追加到目标会话",
          [r["role"] for r in target_rows] == ["user", "assistant", "user", "assistant"])
    db5.close()

    server.shutdown()
    db.close()
    print()
    print("SMOKE " + ("PASS" if not FAILED else "FAIL: " + ", ".join(FAILED)))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
