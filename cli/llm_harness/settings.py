"""配置向导：/setting 在新终端窗口运行，交互式修改 config.toml。"""
import argparse
import os
import tomllib
from pathlib import Path

CONFIG_DIR = Path.home() / ".llm_harness"


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    raise TypeError(f"unsupported type: {type(v)}")


def dump_toml(cfg: dict) -> str:
    lines = ["[defaults]"]
    for k, v in cfg.get("defaults", {}).items():
        lines.append(f"{k} = {_fmt(v)}")
    for p in cfg.get("providers", []):
        lines += ["", "[[providers]]"]
        for k, v in p.items():
            lines.append(f"{k} = {_fmt(v)}")
    return "\n".join(lines) + "\n"


def ask(label: str, current: str) -> str:
    v = input(f"{label} [{current}]: ").strip()
    return v or current


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="llm-harness settings")
    ap.add_argument("--config", help="config.toml 路径")
    args = ap.parse_args(argv)
    path = Path(args.config) if args.config else Path(os.environ.get("LLM_HARNESS_CONFIG", CONFIG_DIR / "config.toml"))

    cfg = {}
    if path.exists():
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    providers = cfg.setdefault("providers", [])
    if not providers:
        providers.append({"name": "default", "base_url": "http://localhost:11434/v1", "api_key_env": "", "models": []})
    defaults = cfg.setdefault("defaults", {})
    print("llm-harness 设置向导（直接回车保持当前值，Ctrl+C 取消）")
    print("管理多个 provider（同 base_url 可配多个，按模型分组）；删除请手动编辑配置文件\n")

    i = 0
    while True:
        i += 1
        if i > len(providers):
            providers.append({"name": "", "base_url": providers[-1].get("base_url", ""),
                              "api_key_env": "", "models": []})
        p = providers[i - 1]
        print(f"--- provider {i}/{len(providers)} ---")
        p["name"] = ask(f"provider {i} 名称", p.get("name", "default"))
        p["base_url"] = ask(f"provider {i} base_url（需含 /v1）", p.get("base_url", "http://localhost:11434/v1"))
        cur_key = p.get("api_key") or f"env:{p.get('api_key_env', '')}" or "无"
        key = input(f"provider {i} api_key [当前: {cur_key}]（输入新 key 或回车保持）: ").strip()
        if key:
            p["api_key"] = key
            p["api_key_env"] = ""
        models = ask(f"provider {i} models（英文逗号分隔）", ", ".join(p.get("models", [])))
        p["models"] = [m.strip() for m in models.split(",") if m.strip()]
        if i >= len(providers) and input("是否再添加一个 provider（y=是，回车=否）: ").strip().lower() != "y":
            break
    providers[:] = [p for p in providers if p.get("name")]

    pairs = [f"{p['name']}:{m}" for p in providers for m in p.get("models", [])]
    cur = defaults.get("model", "")
    if cur and ":" not in cur:
        for p in providers:
            if cur in p.get("models", []):
                cur = f"{p['name']}:{cur}"
                break
    default = ask("默认模型（provider:model，如 deepseek:deepseek_v4_flash）",
                  cur or (pairs[0] if pairs else ""))
    if ":" in default:
        pname, mname = default.split(":", 1)
        defaults["model"] = mname
        defaults["provider"] = pname
    else:
        defaults["model"] = default
        defaults.pop("provider", None)
    defaults["context_window"] = int(ask("上下文窗口 context_window", str(defaults.get("context_window", 262144))) or 262144)
    defaults["max_input"] = int(ask("最大输入 max_input", str(defaults.get("max_input", 10000))) or 10000)
    defaults["max_tokens"] = int(ask("默认输出上限 max_tokens", str(defaults.get("max_tokens", 20000))) or 20000)
    defaults["search_provider"] = ask("联网搜索 provider（firecrawl/tavily）", defaults.get("search_provider", "firecrawl"))
    defaults["firecrawl_api_key"] = ask("Firecrawl API key（联网搜索）", defaults.get("firecrawl_api_key", ""))
    defaults["tavily_api_key"] = ask("Tavily API key（联网搜索）", defaults.get("tavily_api_key", ""))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(cfg), encoding="utf-8")
    print(f"\n已写入: {path}")
    print("回到主程序输入 /reload 生效，然后关闭本窗口。")
    input("按回车关闭窗口...")


if __name__ == "__main__":
    main()
