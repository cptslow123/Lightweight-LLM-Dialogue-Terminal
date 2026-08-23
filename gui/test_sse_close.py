import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "cli"
sys.path.insert(0, str(ROOT))
from llm_harness import api


class MockModel(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for part in ("完", "成"):
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    server = HTTPServer(("127.0.0.1", 0), MockModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = {"base_url": f"http://127.0.0.1:{server.server_address[1]}/v1", "api_key_env": ""}

    async def check():
        result = None
        async for result in api.stream_chat(cfg, "mock", [{"role": "user", "content": "hi"}]):
            pass
        assert result and result.text == "完成", result

    asyncio.run(check())
    server.shutdown()
    print("PASS SSE stream completes")


if __name__ == "__main__":
    main()
