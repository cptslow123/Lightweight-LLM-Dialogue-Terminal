"""冒烟测试：Android web 终端后端（mock LLM + mock Firecrawl 搜索）。"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from android_terminal.server import Handler, ThreadingHTTPServer, load_config  # noqa: E402
from llm_harness import web as web_mod  # noqa: E402

SEARCH_QUERY = "上海今天天气"
FINAL_TEXT = "今天上海多云，25 度。"
FAILED = []


def _msg_text(m):
    c = m.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


class MockLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        has_results = any("[网络搜索结果] 查询:" in _msg_text(m) for m in body.get("messages", [])
                          if m.get("role") == "system")
        text = FINAL_TEXT if has_results else "[SEARCH: " + SEARCH_QUERY + "]"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for ch in text:
            chunk = {"choices": [{"delta": {"content": ch}}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def fake_search(query, max_results=5, api_key=None, provider="firecrawl"):
    return [{"title": "测试标题", "body": "测试正文", "href": "https://example.com"}]


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main():
    web_mod.web_search = fake_search

    llm = HTTPServer(("127.0.0.1", 0), MockLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp())
    cfg_path = tmp / "config.toml"
    cfg_path.write_text(
        f'[defaults]\nmodel = "mock"\nsearch_provider = "firecrawl"\n'
        f'[[providers]]\nname = "mock"\nbase_url = "http://127.0.0.1:{llm.server_address[1]}/v1"\n'
        f'api_key_env = ""\nmodels = ["mock"]\n',
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.cfg = load_config(str(cfg_path))
    server.token = ""
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    with httpx.Client(base_url=base, timeout=30.0) as client:
        r = client.get("/api/config")
        cfg = r.json()
        check("config 返回 provider/model/搜索", r.status_code == 200
              and cfg["providers"][0]["name"] == "mock" and cfg["search"]["provider"] == "firecrawl")

        r = client.post("/api/reset", json={})
        check("reset 清空会话", r.status_code == 200)

        r = client.post("/api/search", json={"query": "手动搜索"})
        results = r.json().get("results", [])
        check("手动搜索注入结果", r.status_code == 200 and results and results[0]["href"] == "https://example.com")

        r = client.post("/api/reset", json={})
        check("reset 二次清空", r.status_code == 200)

        events = []
        with client.stream("POST", "/api/chat",
                           json={"provider": "mock", "model": "mock", "thinking": "off",
                                 "search": True, "message": "上海天气如何？"}) as resp:
            check("chat SSE 200", resp.status_code == 200)
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
        types = [e.get("type") for e in events]
        done = next((e for e in events if e.get("type") == "done"), {})
        search_ev = next((e for e in events if e.get("type") == "search"), {})
        check("自动搜索标记触发搜索事件", "search" in types and search_ev.get("query") == SEARCH_QUERY)
        check("搜索后模型基于结果作答", done.get("text") == FINAL_TEXT)

    server.shutdown()
    llm.shutdown()
    if FAILED:
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
