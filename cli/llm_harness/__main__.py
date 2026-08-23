"""Entry point: python -m llm_harness"""
import argparse
import os
import tomllib
from pathlib import Path

from . import __version__, app
from .db import DB

CONFIG_DIR = Path.home() / ".llm_harness"


def config_path(path: str | None = None) -> Path:
    return Path(path) if path else Path(os.environ.get("LLM_HARNESS_CONFIG", CONFIG_DIR / "config.toml"))


def load_config(path: str | Path | None = None) -> dict:
    p = config_path(path)
    if not p.exists():
        return {"defaults": {}, "providers": [{"name": "default", "base_url": "http://localhost:11434/v1",
                                               "api_key_env": "", "models": [""]}]}
    with open(p, "rb") as f:
        return tomllib.load(f)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="llm-harness", description="Lightweight terminal LLM harness")
    ap.add_argument("--version", action="version", version=f"llm-harness {__version__}")
    ap.add_argument("--config", help="config.toml 路径")
    ap.add_argument("--provider", help="provider 名称")
    ap.add_argument("--model", help="模型名称")
    ap.add_argument("--think", choices=["off", "low", "medium", "high"], help="思考档位")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    db_path = Path(os.environ.get("LLM_HARNESS_DB", CONFIG_DIR / "chat.db"))
    db = DB(db_path)
    try:
        app.App(cfg, db, provider=args.provider, model=args.model, thinking=args.think,
                config_path=str(config_path(args.config))).run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
