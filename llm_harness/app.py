"""Terminal REPL: input/completion -> commands -> streaming -> persistence."""
import asyncio
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown, TableElement
from rich.table import Table
from rich.text import Text


class _WrapTable(TableElement):
    """表格单元格超宽时换行（fold）而不是省略号截断，避免正文内容被隐藏。"""

    def __rich_console__(self, console, options):
        for item in super().__rich_console__(console, options):
            if isinstance(item, Table):
                for col in item.columns:
                    col.overflow = "fold"
            yield item


class MarkdownWrap(Markdown):
    elements = {**Markdown.elements, "table_open": _WrapTable}

from prompt_toolkit.completion import Completer, Completion, PathCompleter

from . import __version__, api, ctx, web

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
COMMANDS = ["help", "new", "list", "switch", "rename", "delete", "clear",
            "fork", "load", "model", "think", "attach", "compress", "usage", "info", "setting", "reload", "web", "unfold", "fold", "quit"]

HELP_TEXT = """\
[bold]llm-harness[/bold] 命令：
  /new [标题]           新建会话
  /list                 列出会话
  /switch <id>          切换会话
  /rename <id> <标题>   重命名
  /delete <id>          删除会话
  /clear                清空当前会话消息
  /fork [标题]          复制当前会话为分支
  /load <id> [N]        把另一会话最近 N 条插入当前上下文
  /model <名称>         切换模型（Tab 补全）
  /think <off|low|medium|high>  思考档位
  /attach <路径...>     发送图片/文件（也可直接拖入终端）
  /compress [N]         手动压缩最早 N 条消息为摘要
  /usage                查看上下文占用
  /unfold               展开本次思考链（完整内容）
  /fold                重新折叠思考链（只显示尾部）
  /info                 查看当前配置（窗口/输入/输出限制）
  /web <搜索词>         联网搜索，结果注入上下文
  /setting              新窗口打开配置向导（多 provider / key / url / 模型）
  /reload               重新加载配置文件（/setting 后使用）
  /quit /exit           退出
  生成中 Ctrl+C 中断；输入中 Ctrl+C 清空
"""

# 自动联网搜索：模型需要实时信息时，第一行输出 [SEARCH: 查询词]，harness 检测后自动搜索并注入结果
AUTO_SEARCH_PROMPT = (
    "\n\n[自动联网搜索规则]\n"
    "你默认倾向联网搜索：只要问题涉及事实性、时效性或可能变化的信息，都优先搜索，而不是凭记忆回答。宁可多搜，不要凭过时记忆猜测。\n"
    "以下情况必须搜索：\n"
    "1. 涉及最新信息（新闻、天气、股票、价格、版本发布、事件进展、产品发布等）；\n"
    "2. 答案可能随日期、环境或外部状态变化；\n"
    "3. 涉及具体事实、人物、公司、产品、数据等，而你没有十足把握；\n"
    "4. 用户提到 搜索/最新/现在/当前/最近/新 等字眼。\n"
    "即使你比较确定答案，只要是事实性且可能过时的问题，也建议搜索核实一遍。\n"
    "需要搜索时，请在回答的第一行只输出 [SEARCH: 搜索词]（尽量简洁、含关键词，不含其他内容），然后停止。\n"
    "harness 会执行搜索并把结果作为[网络搜索结果]注入，请基于搜索结果作答并标注来源；"
    "如果搜索结果仍无法回答，请如实说明原因。\n"
    "只有纯常识、数学计算、个人建议等完全不依赖外部信息的问题才可以直接回答。"
)
SEARCH_RE = re.compile(r"^\s*\[SEARCH:\s*([^\]]+?)\]\s*", re.MULTILINE)
SEARCH_PREFIX_RE = re.compile(r"^\s*\[SEARCH:")
def _safe_flush_len(text: str) -> int:
    """返回可安全渲染的 Markdown 前缀长度：代码围栏闭合处或空行结束的完整块。"""
    lines = text.splitlines(keepends=True)
    fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fence = not fence
            if not fence:
                return sum(len(l) for l in lines[: i + 1])
        elif not fence and i > 0 and ln.strip() == "":
            return sum(len(l) for l in lines[: i + 1])
    return 0


def _rz_row(line: str, width: int) -> Text:
    """折叠面板行：截断到终端宽度并用空格补齐，保证原地覆盖干净。"""
    t = Text(line, style="dim")
    t.truncate(max(width - 2, 10), overflow="ellipsis")
    pad = width - cell_len(t.plain)
    if pad > 0:
        t.append(" " * pad)
    return t


def _cursor_up(console, rows: int):
    """终端光标上移 rows 行（原地重绘折叠面板用）。"""
    if rows <= 0:
        return
    try:
        console.file.write(f"\x1b[{rows}A")
        console.file.flush()
    except Exception:
        pass


SEARCH_DIRECTIVE = """

请直接基于以上搜索结果回答用户的问题，不要再输出 [SEARCH: ...]。如果搜索结果仍无法回答，请如实说明原因。"""

# 按空白分词，保留引号内的空格并去掉引号（支持终端拖入带空格的文件路径）
_PATH_TOKEN_RE = re.compile(r'"(?:[^"]*)"|\'(?:[^\']*)\'|\S+')

def split_terms(line: str) -> list:
    out = []
    for m in _PATH_TOKEN_RE.finditer(line):
        t = m.group(0)
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        out.append(t)
    return out

def classify_attachment(path: str):
    """返回 'image' / 'file' / None（不支持的扩展名按普通文本处理）。"""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in api.FILE_EXTS:
        return "file"
    return None


class HarnessCompleter(Completer):
    """Tab 补全：命令 / 模型 / 会话 id / 思考档位 / 图片路径。"""

    def __init__(self, app):
        self.app = app

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.strip()
        if not stripped.startswith("/"):
            return
        token = re.split(r"\s+", text)[-1] if text.strip() else ""
        parts = stripped.split()
        first = parts[0][1:] if parts else ""
        if len(parts) <= 1 and not text.endswith((" ", "\t")):
            # 补全命令本身（token 含前导 /，可整体替换）
            for cmd in COMMANDS:
                full = "/" + cmd
                if full.startswith(token) and full != token:
                    yield Completion(full, start_position=-len(token))
            return
        if len(parts) >= 2 or text.endswith((" ", "\t")):
            if first == "model":
                for m in self.app.all_model_pairs():
                    if m.startswith(token) and m != token:
                        yield Completion(m, start_position=-len(token))
            elif first == "think":
                for lv in ("off", "low", "medium", "high"):
                    if lv.startswith(token) and lv != token:
                        yield Completion(lv, start_position=-len(token))
            elif first in ("switch", "load", "delete", "rename"):
                for sid, title in self.app.sessions:
                    s = str(sid)
                    if (s.startswith(token) or title.startswith(token)) and s != token:
                        label = title if first == "load" else f"{s}  {title}"
                        yield Completion(s, start_position=-len(token), display=label)
            elif first == "attach":
                yield from PathCompleter().get_completions(document, complete_event)


class App:
    def __init__(self, cfg, db, provider=None, model=None, thinking=None, console=None, config_path=None):
        self.cfg = cfg
        self.config_path = config_path
        self.db = db
        self.console = console or Console()
        self.cli_provider = provider
        self.cli_model = model
        self.cli_thinking = thinking
        self.conv = None
        self.sessions = []
        self.last_reasoning = ""

    # ---- 状态 ----
    @property
    def defaults(self):
        return self.cfg.get("defaults", {})

    @property
    def providers(self):
        return self.cfg.get("providers", [])

    @property
    def provider_name(self):
        return self.cli_provider or (self.conv["provider"] if self.conv else "") \
            or self.defaults.get("provider", "") \
            or (self.providers[0]["name"] if self.providers else "default")

    def provider_cfg(self):
        name = self.provider_name
        for p in self.providers:
            if p["name"] == name:
                return p
        return {"name": name, "base_url": "http://localhost:11434/v1", "api_key_env": "", "models": []}

    def tavily_key(self):
        return self.defaults.get("tavily_api_key") or os.environ.get("TAVILY_API_KEY", "")

    def resolve_model(self):
        models = self.provider_cfg().get("models", [])
        return self.cli_model or (self.conv["model"] if self.conv else "") \
            or self.defaults.get("model") or (models[0] if models else "")

    def resolve_thinking(self):
        return self.cli_thinking or (self.conv["thinking"] if self.conv else "") \
            or self.defaults.get("thinking", "off")

    def load_msgs(self):
        return [api.Msg(role=r["role"], parts=json.loads(r["content"]), id=r["id"],
                        hidden=bool(r["hidden"]), summary=bool(r["summary"]))
                for r in self.db.get_messages(self.conv["id"])]

    def all_model_pairs(self):
        return sorted({f"{p['name']}:{m}" for p in self.providers for m in p.get("models", [])})

    def refresh(self):
        self.sessions = [(r["id"], r["title"]) for r in self.db.list_conversations(100)]

    # ---- 启动 ----
    def load_session(self, cid=None):
        conv = self.db.get_conversation(cid) if cid else self.db.latest_conversation()
        if conv is None:
            model = self.defaults.get("model", "")
            provider = next((p["name"] for p in self.providers if model in p.get("models", [])),
                            (self.providers[0]["name"] if self.providers else "default"))
            cid = self.db.new_conversation(
                provider=provider,
                model=model,
                thinking=self.defaults.get("thinking", "off"),
            )
            conv = self.db.get_conversation(cid)
        self.conv = conv

    def run(self):
        self.load_session()
        self.refresh()
        self.console.print(f"[bold]llm-harness v{__version__}[/bold]  模型 [cyan]{self.resolve_model()}[/cyan]  "
                           f"思考 [cyan]{self.resolve_thinking()}[/cyan]")
        self.console.print("[dim]输入 /help 查看命令 · 拖入或粘贴图片/文件路径即可发送 · Ctrl+C 清空当前输入 · Tab 补全[/dim]")
        while True:
            self.enable_cursor_blink()
            try:
                line = self.read_line()
            except KeyboardInterrupt:
                self.console.print()
                continue
            except EOFError:
                self.console.print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self.dispatch(line):
                    break
            else:
                self.handle_input(line)
        # 关闭会话：清空所有对话记录
        self.db.wipe_all()

    @staticmethod
    def enable_cursor_blink():
        # 尝试启用光标闪烁（Windows Terminal 支持；旧 conhost 由系统设置控制）
        try:
            sys.stdout.write("\x1b[?12h\x1b[5 q")
            sys.stdout.flush()
        except Exception:
            pass

    @staticmethod
    def _patch_blink():
        # prompt_toolkit 每次渲染显示光标时发送 \x1b[?12l 会关闭闪烁，
        # 覆盖掉 enable_cursor_blink() 的设置；这里去掉该序列以保持细线光标闪烁。
        try:
            from prompt_toolkit.output import vt100 as _vt
            if getattr(_vt.Vt100_Output.show_cursor, "_lh_keep_blink", False):
                return
            def _show_cursor(self):
                if self._cursor_visible in (False, None):
                    self._cursor_visible = True
                    self.write_raw("\x1b[?25h")
            _show_cursor._lh_keep_blink = True
            _vt.Vt100_Output.show_cursor = _show_cursor
        except Exception:
            pass

    def read_line(self):
        # TTY 下用 prompt_toolkit 提供 Tab 补全；管道/重定向时退回原生 input()
        model = self.resolve_model()
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                from prompt_toolkit import prompt
                from prompt_toolkit.formatted_text import HTML
                self._patch_blink()
                return prompt(HTML(f"<ansibrightblue>{html.escape(model)}</ansibrightblue> > "),
                              completer=HarnessCompleter(self)).strip()
            except Exception:
                pass
        return input(f"{model} > ").strip()

    # ---- 输入 ----
    def handle_input(self, line):
        images, files, text_parts = [], [], []
        for term in split_terms(line):
            if Path(term).is_file():
                kind = classify_attachment(term)
                if kind == "image":
                    images.append(term)
                    continue
                if kind == "file":
                    files.append(term)
                    continue
            text_parts.append(term)
        text = " ".join(text_parts).strip()
        if not text and not images and not files:
            self.console.print("[dim](未识别到文字或附件路径)[/dim]")
            return
        try:
            asyncio.run(self.send(text, images, files))
        except KeyboardInterrupt:
            self.console.print("[dim]生成已中断[/dim]")

    # ---- 核心发送 ----
    async def send(self, text, images, files=None):
        conv = self.db.get_conversation(self.conv["id"])
        cfg = self.provider_cfg()
        model = self.resolve_model()
        thinking = self.resolve_thinking()
        system_prompt = conv["system_prompt"] or self.defaults.get("system_prompt", "You are a helpful assistant.")
        system_prompt += f"\n当前日期：{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日"
        system_prompt += AUTO_SEARCH_PROMPT
        window = int(self.defaults.get("context_window", 8000))
        threshold = float(self.defaults.get("compress_threshold", 0.75))
        keep_last = int(self.defaults.get("compress_keep_last", 20))
        max_tokens = int(self.defaults.get("max_tokens", 4096))
        max_input = int(self.defaults.get("max_input", 10000))

        files = files or []
        if images:
            self.console.print("[dim](图片: " + ", ".join(Path(i).name for i in images) + ")[/dim]")
        if files:
            self.console.print("[dim](附件: " + ", ".join(Path(f).name for f in files) + ")[/dim]")

        parts = api.parts_from_inputs(text, images)
        for fp in files:
            try:
                content = api.extract_text(fp)
            except Exception as e:
                self.console.print(f"[red]附件 {Path(fp).name} 读取失败：{e}[/red]")
                continue
            if len(content) > api.MAX_FILE_CHARS:
                content = content[:api.MAX_FILE_CHARS] + "\n…（内容过长已截断）"
            parts.append({"type": "text", "text": f"[附件: {Path(fp).name}]\n{content}"})
        user_msg = api.Msg(role="user", parts=parts)

        async def do_round(include_user: bool):
            msgs = self.load_msgs() + ([user_msg] if include_user else [])
            send_msgs, stats = await ctx.prepare(msgs, system_prompt, cfg, model, window, threshold, keep_last,
                                                 reserve=max_tokens, max_input=max_input)
            for at_id, _, ids in stats["summaries"]:
                self.db.mark_hidden(self.conv["id"], ids)
            for at_id, sum_text, _ in stats["summaries"]:
                self.db.add_message(self.conv["id"], "system",
                                    [{"type": "text", "text": "[早期对话摘要] " + sum_text}], summary=1, at=at_id)
            return send_msgs, stats

        send, stats = await do_round(include_user=True)
        self.db.add_message(self.conv["id"], "user", user_msg.parts)
        if self.conv["title"] == "untitled" and text:
            self.db.update_conversation(self.conv["id"], title=text[:30])
        self.db.update_conversation(self.conv["id"], provider=self.provider_name, model=model, thinking=thinking)
        self.conv = self.db.get_conversation(self.conv["id"])
        self.refresh()

        t0 = time.perf_counter()
        search_round = 0
        last_context = ""
        while True:
            search_round += 1
            assistant_text, reasoning, usage, error = await self.stream_render(cfg, model, send, thinking, max_tokens)
            m = SEARCH_RE.match(assistant_text) if assistant_text and not error else None
            if m and search_round < 2:
                # 搜索标记轮：静默联网，不打印思考链/标记，只留一行过渡提示
                query = m.group(1).strip()
                self.console.print(f"[dim]已联网搜索: {query}[/dim]")
                try:
                    results = web.web_search(query, 5, self.tavily_key())
                except Exception as e:
                    self.console.print(f"[red]自动搜索失败: {e}[/red]")
                    break
                if not results:
                    self.console.print("[red]没有搜索结果[/red]")
                    break
                lines = [f"[网络搜索结果] 查询: {query}"]
                for i, r in enumerate(results, 1):
                    lines.append(f"\n{i}. {r.get('title', '')}")
                    body = (r.get("body") or "").strip()
                    if body:
                        lines.append(f"   {body[:400]}")
                    if r.get("href"):
                        lines.append(f"   来源: {r['href']}")
                results_text = "\n".join(lines)
                last_context = results_text
                self.db.add_message(self.conv["id"], "system",
                                    [{"type": "text", "text": results_text + SEARCH_DIRECTIVE}])
                self.refresh()
                send, stats = await do_round(include_user=False)
                continue
            if error:
                if assistant_text:
                    self.db.add_message(self.conv["id"], "assistant", [{"type": "text", "text": assistant_text}])
            elif m:
                # 模型第二轮仍输出搜索标记：不保存标记，直接展示搜索结果兜底
                if last_context:
                    self.console.print("[yellow]模型未直接作答，已展示搜索结果：[/yellow]")
                    self.console.print(MarkdownWrap(last_context))
            else:
                if assistant_text:
                    self.db.add_message(self.conv["id"], "assistant", [{"type": "text", "text": assistant_text}])
            break
        elapsed = time.perf_counter() - t0
        self.print_usage(stats, window, usage, max_tokens, max_input, elapsed)
        if error:
            self.console.print(f"[red]{error}[/red]")
            if "网络错误" in error or "network error" in error:
                self.console.print("[dim]提示：请检查网络连接；/info 查看 base_url 与模型；/setting 重新配置[/dim]")

    async def stream_render(self, cfg, model, send, thinking, max_tokens=None):
        text, reasoning, usage, error = "", "", None, None
        interrupted = False
        self.last_reasoning = ""
        t0 = time.perf_counter()
        pending = ""
        consumed = 0
        reasoning_shown = False
        rz_printed = 0        # 已按完整行消费的 reasoning 长度
        rz_streaming = False  # reasoning 正在流式显示时暂停 ticker
        rz_total = 0          # reasoning 完整行总数
        rz_window = []        # 最近 5 行文本（面板只显示这些，其余折叠）
        rz_panel_rows = 0     # 当前折叠面板占用的终端行数（0 = 未绘制）
        rz_finalized = False  # 面板是否已收尾成折叠视图
        rz_tty = bool(getattr(self.console.file, "isatty", lambda: False)())
        status = ""

        def show_status(msg):
            nonlocal status
            status = msg
            self.console.print(Text(msg.ljust(50)), end="\r")

        def clear_status():
            nonlocal status
            if status:
                self.console.print(Text(" " * 50), end="\r")
                status = ""

        # 上游慢（首 token 前/停顿）时也要显示处理时长：每 0.5s 刷新一次
        async def status_ticker():
            try:
                while True:
                    await asyncio.sleep(0.5)
                    if not rz_streaming:
                        show_status(f"… 处理中 {time.perf_counter() - t0:.1f}s")
            except asyncio.CancelledError:
                pass

        def draw_rz_panel(header: str):
            """原地重绘折叠面板：头部状态行 + 最近 5 行。"""
            nonlocal rz_panel_rows
            width = self.console.width or 80
            _cursor_up(self.console, rz_panel_rows)
            self.console.print(_rz_row(header, width))
            for ln in rz_window:
                self.console.print(_rz_row(ln, width))
            rz_panel_rows = 1 + len(rz_window)
            try:
                self.console.file.flush()
            except Exception:
                pass

        def finalize_rz_panel():
            """思考结束：把面板收尾成折叠视图（最近 5 行 + 折叠提示）。"""
            nonlocal rz_panel_rows, rz_finalized
            if rz_finalized or rz_panel_rows == 0:
                return
            rz_finalized = True
            width = self.console.width or 80
            _cursor_up(self.console, rz_panel_rows)
            rows = list(rz_window)
            if rz_total > 5:
                rows.append(f"… 共 {rz_total} 行，输入 /unfold 展开")
            while len(rows) < rz_panel_rows:
                rows.append("")
            for row in rows[:rz_panel_rows]:
                self.console.print(_rz_row(row, width))
            try:
                self.console.file.flush()
            except Exception:
                pass

        def flush_reasoning(reasoning: str):
            """新完成的完整行进入滚动折叠面板：最多显示 5 行，多的自动折叠。"""
            nonlocal rz_printed, rz_streaming, reasoning_shown, rz_total, rz_window, rz_finalized
            if rz_finalized:
                return
            new = reasoning[rz_printed:]
            idx = new.rfind("\n")
            if idx < 0:
                return  # 还没有完整行
            if not rz_streaming:
                clear_status()
                rz_streaming = True
            completed = new[:idx].split("\n")
            rz_total += len(completed)
            for ln in completed:
                rz_window.append(ln)
            del rz_window[:-5]
            rz_printed = len(reasoning) - (len(new) - idx - 1)
            reasoning_shown = True
            if not rz_tty:  # 非交互输出（管道/测试）：退化为逐行打印
                for ln in completed:
                    self.console.print(Text(ln, style="dim"))
                return
            draw_rz_panel(f"思考中 · 已 {rz_total} 行 · {time.perf_counter() - t0:.1f}s")

        ticker = asyncio.create_task(status_ticker())

        try:
            async for block in api.stream_chat(cfg, model, send, thinking, max_tokens):
                if block.error:
                    error = block.error
                    break
                text, usage = block.text, block.usage
                reasoning = block.reasoning.replace("\r", "")
                self.last_reasoning = reasoning
                if SEARCH_PREFIX_RE.match(text):
                    show_status(f"… 生成中 {time.perf_counter() - t0:.1f}s")
                    continue
                if not text:
                    if reasoning:
                        flush_reasoning(reasoning)
                    continue
                clear_status()
                rz_streaming = False
                if rz_tty and reasoning_shown:
                    finalize_rz_panel()
                if reasoning and not reasoning_shown:
                    reasoning_shown = True
                    self.console.print(self.reasoning_view(reasoning))
                if len(text) > consumed:
                    pending += text[consumed:].replace("\r", "").replace("\x1b", "")
                    consumed = len(text)
                while pending:
                    n = _safe_flush_len(pending)
                    if n == 0 and len(pending) < 3000:
                        break
                    if n == 0:
                        idx = pending.rfind("\n")
                        n = idx + 1 if idx >= 0 else len(pending)
                    self.console.print(MarkdownWrap(pending[:n]))
                    pending = pending[n:]
                if pending:
                    show_status(f"… 生成中 {time.perf_counter() - t0:.1f}s")
        except KeyboardInterrupt:
            interrupted = True
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass  # ticker 未启动即被取消时 await 会直接抛 CancelledError
            clear_status()
            if rz_tty and reasoning_shown and rz_panel_rows and not rz_finalized:
                finalize_rz_panel()
            if reasoning and not reasoning_shown and not SEARCH_PREFIX_RE.match(text):
                self.console.print(self.reasoning_view(reasoning))
            if not SEARCH_RE.match(text) and pending:
                self.console.print(MarkdownWrap(pending))
                pending = ""
        if interrupted:
            self.console.print("[dim]（已中断，保留已生成部分）[/dim]")
        return text, reasoning, usage, error

    @staticmethod
    def reasoning_view(reasoning):
        lines = reasoning.splitlines()
        total = len(lines)
        shown = lines[-5:] if total > 5 else lines
        view = Text(style="dim")
        for i, ln in enumerate(shown):
            if i:
                view.append("\n")
            view.append_text(Text(ln))
        if total > 5:
            view.append(f"\n… 共 {total} 行，输入 /unfold 展开")
        return view

    def print_usage(self, stats, window, usage=None, reserve=0, max_input=0, elapsed=None):
        budget = min(window - reserve, max_input) if max_input else window - reserve
        line = f"[dim]tokens ≈ {stats['estimated']} / {budget}"
        if reserve:
            line += f" · 输出预留 {reserve}"
        if usage:
            line += f" · api {usage.get('total_tokens', '?')}"
        if elapsed is not None:
            line += f" · 耗时 {elapsed:.1f}s"
        if stats["compressed"]:
            line += f" · 压缩 {stats['compressed']} 条"
        if stats["dropped"]:
            line += f" · 截断 {stats['dropped']} 条"
        self.console.print(line + "[/dim]")

    # ---- 命令 ----
    def dispatch(self, line) -> bool:
        words = split_terms(line)
        cmd, args = words[0][1:].lower(), words[1:]
        handler = getattr(self, f"cmd_{cmd}", None)
        if handler is None:
            self.console.print(f"[yellow]未知命令 /{cmd}，输入 /help 查看[/yellow]")
            return True
        return handler(args)

    def cmd_help(self, args):
        self.console.print(HELP_TEXT)
        return True

    def cmd_quit(self, args):
        self.console.print("[dim]再见[/dim]")
        return False

    cmd_exit = cmd_quit

    def cmd_new(self, args):
        title = " ".join(args) or "untitled"
        cid = self.db.new_conversation(title=title, provider=self.provider_name, model=self.resolve_model(),
                                       thinking=self.resolve_thinking())
        self.conv = self.db.get_conversation(cid)
        self.refresh()
        self.console.print("[green]已新建会话[/green]")
        return True

    def cmd_list(self, args):
        rows = self.db.list_conversations()
        if not rows:
            self.console.print("[dim]暂无会话[/dim]")
            return True
        table = Table(title="会话")
        for col in ("title", "model", "updated_at"):
            table.add_column(col)
        for r in rows:
            table.add_row(r["title"][:30], r["model"], r["updated_at"][:16])
        self.console.print(table)
        return True

    def _conv_id(self, args):
        if not args:
            self.console.print("[yellow]缺少会话 id[/yellow]")
            return None
        try:
            return int(args[0])
        except ValueError:
            self.console.print("[yellow]id 必须是数字[/yellow]")
            return None

    def cmd_switch(self, args):
        if not args:
            self.console.print("[yellow]用法: /switch <id>，输入 /list 查看会话[/yellow]")
            return True
        cid = self._conv_id(args)
        if cid is None:
            return True
        conv = self.db.get_conversation(cid)
        if not conv:
            self.console.print("[red]会话不存在[/red]")
            return True
        self.conv = conv
        self.refresh()
        self.console.print(f"[green]已切换: 「{conv['title']}」[/green]")
        return True

    def cmd_rename(self, args):
        cid = self._conv_id(args)
        if cid is None or len(args) < 2:
            return True
        self.db.update_conversation(cid, title=" ".join(args[1:]))
        if self.conv["id"] == cid:
            self.conv = self.db.get_conversation(cid)
        self.refresh()
        self.console.print("[green]已重命名[/green]")
        return True

    def cmd_delete(self, args):
        cid = self._conv_id(args)
        if cid is None:
            return True
        self.db.delete_conversation(cid)
        if self.conv["id"] == cid:
            self.load_session()
        self.refresh()
        self.console.print("[green]已删除会话[/green]")
        return True

    def cmd_clear(self, args):
        self.db.clear_messages(self.conv["id"])
        self.console.print("[green]已清空当前会话消息[/green]")
        return True

    def cmd_fork(self, args):
        cid = self.db.new_conversation(title=(" ".join(args) or self.conv["title"] + " (fork)"),
                                       system_prompt=self.conv["system_prompt"], provider=self.conv["provider"],
                                       model=self.conv["model"], thinking=self.conv["thinking"])
        for m in self.db.get_messages(self.conv["id"], include_hidden=True):
            self.db.add_message(cid, m["role"], json.loads(m["content"]), m["hidden"], m["summary"])
        self.conv = self.db.get_conversation(cid)
        self.refresh()
        self.console.print("[green]已分支到新会话[/green]")
        return True

    def cmd_load(self, args):
        cid = self._conv_id(args)
        if cid is None:
            return True
        n = None
        if len(args) > 1:
            try:
                n = int(args[1])
            except ValueError:
                pass
        rows = self.db.get_messages(cid)
        if n:
            rows = rows[-n:]
        for m in rows:
            self.db.add_message(self.conv["id"], m["role"], json.loads(m["content"]))
        self.refresh()
        self.console.print(f"[green]已载入 {len(rows)} 条消息，当前会话记录：[/green]")
        self.render_history()
        return True

    def render_history(self):
        """重绘当前会话的用户/助手消息（跳过 system 摘要与联网注入）。"""
        shown = 0
        for r in self.db.get_messages(self.conv["id"]):
            role, content = r["role"], json.loads(r["content"])
            if role == "system":
                continue
            text = "".join(p.get("text", "") for p in content if p.get("type") == "text").strip()
            imgs = sum(1 for p in content if p.get("type") == "image_url")
            if not text and not imgs:
                continue
            shown += 1
            label = "你" if role == "user" else self.resolve_model()
            self.console.print(f"[bold cyan]{label}:[/bold cyan]")
            if imgs:
                self.console.print(f"[dim](图片 {imgs} 张)[/dim]")
            if text:
                if role == "assistant":
                    self.console.print(MarkdownWrap(text))
                else:
                    self.console.print(text)
            self.console.print()
        if not shown:
            self.console.print("[dim]当前会话暂无消息[/dim]")
        return True

    def cmd_model(self, args):
        if not args:
            self.console.print(f"[dim]当前模型: {self.resolve_model()}[/dim]")
            avail = self.all_model_pairs()
            if avail:
                self.console.print("[dim]可用模型: " + ", ".join(avail) + "[/dim]")
            return True
        name = args[0]
        if ":" in name:
            pname, mname = name.split(":", 1)
            for p in self.providers:
                if p["name"] == pname:
                    self.db.update_conversation(self.conv["id"], provider=pname, model=mname)
                    self.conv = self.db.get_conversation(self.conv["id"])
                    self.refresh()
                    self.console.print(f"[green]模型已切换: {pname}:{mname}[/green]")
                    return True
            self.console.print(f"[yellow]未找到 provider: {pname}[/yellow]")
            return True
        for p in self.providers:
            if name in p.get("models", []):
                self.db.update_conversation(self.conv["id"], provider=p["name"], model=name)
                self.conv = self.db.get_conversation(self.conv["id"])
                self.refresh()
                self.console.print(f"[green]模型已切换: {name} ({p['name']})[/green]")
                return True
        self.db.update_conversation(self.conv["id"], model=name)
        self.conv = self.db.get_conversation(self.conv["id"])
        self.console.print(f"[green]模型已切换: {name}[/green]")
        return True

    def cmd_think(self, args):
        if not args:
            self.console.print(f"[dim]当前思考档位: {self.resolve_thinking()}（off | low | medium | high）[/dim]")
            return True
        level = args[0].lower()
        if level not in ("off", "low", "medium", "high"):
            self.console.print("[yellow]档位: off | low | medium | high[/yellow]")
            return True
        self.db.update_conversation(self.conv["id"], thinking=level)
        self.conv = self.db.get_conversation(self.conv["id"])
        self.console.print(f"[green]思考档位: {level}[/green]")
        return True

    def cmd_attach(self, args):
        images, files = [], []
        for p in args:
            if Path(p).is_file():
                kind = classify_attachment(p)
                if kind == "image":
                    images.append(p)
                elif kind == "file":
                    files.append(p)
        if not images and not files:
            self.console.print("[yellow]用法: /attach <图片/文件路径>，或直接把文件拖入终端[/yellow]")
            return True
        asyncio.run(self.send("", images, files))
        return True

    def cmd_web(self, args):
        if not args:
            self.console.print("[yellow]用法: /web <搜索词>[/yellow]")
            return True
        query = " ".join(args)
        self.console.print(f"[dim]正在搜索: {query}[/dim]")

        async def do():
            try:
                results = web.web_search(query, 5, self.tavily_key())
            except Exception as e:
                self.console.print(f"[red]搜索失败: {e}[/red]")
                return
            if not results:
                self.console.print("[red]没有搜索结果[/red]")
                return
            lines = [f"[网络搜索结果] 查询: {query}"]
            for i, r in enumerate(results, 1):
                lines.append(f"\n{i}. {r.get('title', '')}")
                body = (r.get("body") or "").strip()
                if body:
                    lines.append(f"   {body[:400]}")
                if r.get("href"):
                    lines.append(f"   来源: {r['href']}")
            context = "\n".join(lines)
            self.db.add_message(self.conv["id"], "system", [{"type": "text", "text": context}])
            self.refresh()
            await self.send(query, [])

        asyncio.run(do())
        return True

    def cmd_compress(self, args):
        n = None
        if args:
            try:
                n = int(args[0])
            except ValueError:
                pass

        async def do():
            cfg = self.provider_cfg()
            model = self.resolve_model()
            batch = [m for m in self.load_msgs() if not m.summary and m.role != "system"][: (n or 20)]
            if not batch:
                self.console.print("[dim]没有可压缩的消息[/dim]")
                return
            text = await ctx.compress(batch, cfg, model)
            if not text:
                self.console.print("[red]压缩失败[/red]")
                return
            at = batch[0].id
            self.db.mark_hidden(self.conv["id"], [m.id for m in batch])
            self.db.add_message(self.conv["id"], "system", [{"type": "text", "text": "[早期对话摘要] " + text}], summary=1, at=at)
            self.refresh()
            self.console.print(f"[green]已压缩 {len(batch)} 条消息为摘要[/green]")

        asyncio.run(do())
        return True

    def cmd_info(self, args):
        cfg = self.provider_cfg()
        self.console.print("[bold]当前配置[/bold]")
        self.console.print(f"provider: [cyan]{self.provider_name}[/cyan]  ({cfg.get('base_url', '')})")
        self.console.print(f"model: [cyan]{self.resolve_model()}[/cyan]")
        self.console.print(f"thinking: [cyan]{self.resolve_thinking()}[/cyan]")
        self.console.print(f"context_window: {self.defaults.get('context_window', 8000)}")
        self.console.print(f"max_input: {self.defaults.get('max_input', 10000)}")
        self.console.print(f"max_tokens: {self.defaults.get('max_tokens', 4096)}")
        self.console.print("tavily: " + ("\u5df2\u914d\u7f6e" if self.tavily_key() else "\u672a\u914d\u7f6e"))
        return True

    def cmd_setting(self, args):
        import subprocess
        path = self.config_path or os.environ.get("LLM_HARNESS_CONFIG", str(Path.home() / ".llm_harness" / "config.toml"))
        cmd = [sys.executable, "-m", "llm_harness.settings", "--config", path]
        if os.name == "nt":
            subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        else:
            subprocess.Popen(cmd)
        self.console.print("[green]已在新窗口打开设置向导，完成后回来输入 /reload 生效[/green]")
        return True

    def cmd_reload(self, args):
        import tomllib
        path = self.config_path
        if not path or not Path(path).exists():
            self.console.print("[yellow]未找到配置文件，无法重载[/yellow]")
            return True
        with open(path, "rb") as f:
            self.cfg = tomllib.load(f)
        self.refresh()
        self.console.print("[green]配置已重载[/green]")
        return True

    def cmd_unfold(self, args):
        if not self.last_reasoning:
            self.console.print("[dim]本次没有思考链[/dim]")
            return True
        self.console.print(Text(self.last_reasoning, style="dim"))
        return True

    def cmd_fold(self, args):
        if not self.last_reasoning:
            self.console.print("[dim]本次没有思考链[/dim]")
            return True
        self.console.print(self.reasoning_view(self.last_reasoning))
        return True

    def cmd_usage(self, args):
        msgs = self.load_msgs()
        self.console.print(f"[dim]消息 {len(msgs)} 条 · 估算 {ctx.estimate_messages(msgs)} tokens "
                           f"(窗口 {self.defaults.get('context_window', 8000)})[/dim]")
        return True
