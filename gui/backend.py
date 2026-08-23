"""Local API used by the portable Light Harness desktop window."""
import asyncio
import json
import os
import shutil
import sys
import tomllib
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# GUI reuses the CLI engine, which is kept in the sibling cli directory.
ROOT = Path(__file__).resolve().parents[1] / "cli"
sys.path.insert(0, str(ROOT))
from llm_harness import api, web  # noqa: E402
from llm_harness.app import AUTO_SEARCH_PROMPT, SEARCH_RE, answer_mode_prompt  # noqa: E402
from llm_harness.db import DB  # noqa: E402
from llm_harness.settings import dump_toml  # noqa: E402

# 开发模式数据保存在 GUI 的 data 目录；便携版由 Tauri 显式传入 EXE 同级目录。
APP_DIR = Path(os.environ.get("LIGHT_HARNESS_DATA_DIR", Path(__file__).resolve().parent / "data"))
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DIR / "config.toml"
DB_PATH = APP_DIR / "chat.db"
EXAMPLE_CONFIG = Path(__file__).resolve().parent / "config.example.toml"
if not CONFIG_PATH.exists() and EXAMPLE_CONFIG.exists():
    shutil.copyfile(EXAMPLE_CONFIG, CONFIG_PATH)


def read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"defaults": {}, "providers": []}
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


DB = DB(DB_PATH)


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if part.get("type") == "text")


def public_message(row):
    content = json.loads(row["content"])
    return {"id": row["id"], "role": row["role"], "content": text_content(content), "summary": bool(row["summary"])}


def public_config(cfg: dict) -> dict:
    # The desktop app is deliberately a local, single-user tool. Return the
    # complete editable configuration so imported keys can be inspected and
    # changed in its Settings panel.
    providers = [dict(provider) for provider in cfg.get("providers", [])]
    for provider in providers:
        provider["api_key_configured"] = bool(provider.get("api_key") or provider.get("api_key_env"))
    defaults = dict(cfg.get("defaults", {}))
    for key in ("firecrawl_api_key", "tavily_api_key"):
        defaults[key + "_configured"] = bool(defaults.get(key))
    return {"defaults": defaults, "providers": providers, "paths": {"config": str(CONFIG_PATH), "database": str(DB_PATH)}}


def get_provider(cfg: dict, name: str | None) -> dict:
    name = name or cfg.get("defaults", {}).get("provider")
    providers = cfg.get("providers", [])
    return next((p for p in providers if p.get("name") == name), providers[0] if providers else {})


def dated_system_prompt(conv, defaults: dict, search: bool) -> str:
    prompt = conv["system_prompt"] or defaults.get("system_prompt", "You are a helpful assistant.")
    now = datetime.now(timezone(timedelta(hours=8)))
    weekdays = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
    prompt += f"\n当前时间（Asia/Shanghai）：{now.year}年{now.month}月{now.day}日，{weekdays[now.weekday()]}。"
    return prompt + (AUTO_SEARCH_PROMPT if search else "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            return self.send_json({"ok": True, "app": "light-harness-gui"})
        if path == "/api/config":
            return self.send_json(public_config(read_config()))
        if path == "/api/conversations":
            return self.send_json({"conversations": [dict(row) for row in DB.list_conversations()]})
        if path.startswith("/api/conversations/") and path.endswith("/messages"):
            conversation_id = int(path.split("/")[3])
            return self.send_json({"messages": [public_message(row) for row in DB.get_messages(conversation_id)]})
        return self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        payload = self.read_body()
        if path == "/api/conversations":
            cfg = read_config()
            cid = DB.new_conversation(payload.get("title", "新对话"), provider=payload.get("provider", cfg.get("defaults", {}).get("provider", "default")), model=payload.get("model", cfg.get("defaults", {}).get("model", "")), thinking=payload.get("thinking", cfg.get("defaults", {}).get("thinking", "off")))
            return self.send_json(dict(DB.get_conversation(cid)), 201)
        if path == "/api/chat":
            return self.stream_chat(payload)
        if path == "/api/search":
            return self.manual_search(payload)
        return self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path
        payload = self.read_body()
        if path == "/api/settings":
            current = read_config()
            if isinstance(payload.get("providers"), list):
                old_providers = {row.get("name"): row for row in current.get("providers", [])}
                providers = []
                for incoming in payload["providers"]:
                    name = str(incoming.get("name", "")).strip()
                    if not name:
                        continue
                    old = old_providers.get(name, {})
                    provider = {
                        "name": name,
                        "base_url": str(incoming.get("base_url", "")).strip(),
                        "api_key_env": str(incoming.get("api_key_env", "")).strip(),
                        "models": [str(model).strip() for model in incoming.get("models", []) if str(model).strip()],
                    }
                    api_key = str(incoming.get("api_key", "")).strip()
                    if api_key:
                        provider["api_key"] = api_key
                    elif old.get("api_key"):
                        provider["api_key"] = old["api_key"]
                    providers.append(provider)
                current["providers"] = providers
            if isinstance(payload.get("defaults"), dict):
                defaults = current.setdefault("defaults", {})
                allowed = {"provider", "model", "thinking", "context_window", "max_input", "max_tokens", "search_provider", "system_prompt"}
                for key, value in payload["defaults"].items():
                    if key in allowed:
                        defaults[key] = value
                for key in ("firecrawl_api_key", "tavily_api_key"):
                    value = str(payload["defaults"].get(key, "")).strip()
                    if value:
                        defaults[key] = value
            CONFIG_PATH.write_text(dump_toml(current), encoding="utf-8")
            return self.send_json(public_config(current))
        if path.startswith("/api/conversations/"):
            cid = int(path.rsplit("/", 1)[-1])
            allowed = {key: value for key, value in payload.items() if key in {"title", "model", "provider", "thinking", "system_prompt"}}
            DB.update_conversation(cid, **allowed)
            return self.send_json(dict(DB.get_conversation(cid)))
        return self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/conversations/"):
            DB.delete_conversation(int(path.rsplit("/", 1)[-1]))
            return self.send_json({"ok": True})
        return self.send_json({"error": "Not found"}, 404)

    def emit(self, payload):
        self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def manual_search(self, payload):
        cfg = read_config(); defaults = cfg.get("defaults", {}); query = payload.get("query", "").strip()
        if not query:
            return self.send_json({"error": "请输入搜索内容"}, 400)
        try:
            provider = defaults.get("search_provider", "firecrawl")
            results = web.web_search(query, 5, defaults.get(f"{provider}_api_key"), provider)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 400)
        return self.send_json({"results": results})

    def stream_chat(self, payload):
        cfg = read_config(); defaults = cfg.get("defaults", {}); cid = payload.get("conversation_id")
        if not cid:
            cid = DB.new_conversation("新对话", model=payload.get("model", defaults.get("model", "")), thinking=payload.get("thinking", defaults.get("thinking", "off")))
        conv = DB.get_conversation(cid); user_text = api.clean_text(payload.get("message", "").strip())
        if not user_text:
            return self.send_json({"error": "请输入消息"}, 400)
        provider = get_provider(cfg, payload.get("provider") or conv["provider"]); model = payload.get("model") or conv["model"] or defaults.get("model", "")
        thinking = payload.get("thinking") or conv["thinking"] or defaults.get("thinking", "off")
        DB.add_message(cid, "user", [{"type": "text", "text": user_text}])
        if conv["title"] in {"untitled", "新对话"}:
            DB.update_conversation(cid, title=user_text[:30])
        DB.update_conversation(cid, provider=provider.get("name", "default"), model=model, thinking=thinking)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream; charset=utf-8"); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "close"); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
        async def run():
            history = [{"role": row["role"], "content": json.loads(row["content"])} for row in DB.get_messages(cid)]
            prompt = dated_system_prompt(conv, defaults, bool(payload.get("search", True)))
            text = ""; reasoning = ""; searches = 0; seen = set(); usage = None
            while True:
                emitted = ""; emitted_len = 0; error = None
                async for block in api.stream_chat(provider, model, [{"role": "system", "content": prompt}, *history], thinking, defaults.get("max_tokens")):
                    if block.reasoning[len(reasoning):]:
                        reasoning = block.reasoning; self.emit({"type": "reasoning", "text": reasoning})
                    if block.text[emitted_len:]:
                        delta = block.text[emitted_len:]
                        emitted = block.text
                        emitted_len = len(emitted)
                        self.emit({"type": "delta", "text": delta})
                    usage = block.usage or usage; error = block.error or error
                if error:
                    self.emit({"type": "error", "message": error}); return
                text = emitted
                match = SEARCH_RE.match(text) if payload.get("search", True) else None
                if not match or searches >= 3:
                    DB.add_message(cid, "assistant", [{"type": "text", "text": text}])
                    self.emit({"type": "done", "text": text, "reasoning": reasoning, "usage": usage, "conversation_id": cid}); return
                query = match.group(1).strip(); key = " ".join(query.lower().split())
                if key in seen:
                    prompt = dated_system_prompt(conv, defaults, False) + answer_mode_prompt(0); continue
                self.emit({"type": "search", "query": query})
                try:
                    search_provider = defaults.get("search_provider", "firecrawl")
                    results = web.web_search(query, 5, defaults.get(f"{search_provider}_api_key"), search_provider)
                except Exception as exc:
                    self.emit({"type": "error", "message": f"联网搜索失败：{exc}"}); return
                context = "[网络搜索结果] 查询: " + query + "\n" + "\n".join(f"{index}. {row.get('title', '')}\n{row.get('body', '')}\n来源: {row.get('href', '')}" for index, row in enumerate(results, 1))
                DB.add_message(cid, "system", [{"type": "text", "text": context}]); history.append({"role": "system", "content": [{"type": "text", "text": context}]}); searches += 1; seen.add(key); prompt = dated_system_prompt(conv, defaults, False) + answer_mode_prompt(3 - searches); text = ""
        try:
            asyncio.run(run())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            # A completed SSE response must close, otherwise the WebView keeps
            # the reader open and the composer remains in its stop state.
            self.close_connection = True


if __name__ == "__main__":
    port = int(os.environ.get("LH_PORT", "18765"))
    # SQLite connection is deliberately shared by the GUI process. A single
    # request loop keeps it on its creating thread and avoids empty 500
    # responses from SQLite's cross-thread protection.
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"LIGHT_HARNESS_PORT={server.server_address[1]}", flush=True)
    server.serve_forever()
