# llm-harness 开发计划

滚屏式终端 LLM harness：多 provider（OpenAI 兼容）、流式对话、思考强度、图片上传、多会话管理、上下文压缩。**不做 tool calling、不做 Anthropic 协议、不做全屏 TUI、不做导入导出。**

## 技术栈
- Python 3.11+；`httpx`（流式）、`rich`（渲染）、`prompt_toolkit`（输入+补全）、`pillow`（图片）；存储 `sqlite3`、配置 `tomllib`

## 项目结构
```text
llm_harness/
├── __main__.py    # 入口：参数解析、配置加载
├── app.py         # REPL：输入(补全)→命令→流式渲染→落库
├── api.py         # OpenAI 兼容流式、thinking 映射、图片编码
├── ctx.py         # token 估算、窗口裁剪、上下文压缩
├── db.py          # SQLite 会话/消息
└── tests/smoke.py # mock 服务器冒烟测试
```
预计源码约 880 行，依赖 4 个库。

## 数据模型
- `conversations`：id、title、system_prompt、provider、model、thinking、created_at、updated_at
- `messages`：id、conversation_id、role、content(JSON)、hidden、summary、created_at
- 消息不可变：压缩只标记 `hidden`，原始记录保留；摘要消息 `summary=1` 且不可再压缩

## 命令
`/new` `/list` `/switch` `/rename` `/delete` `/clear` `/fork` `/load <id> [N]` `/model` `/think` `/attach` `/compress` `/usage` `/quit`

## 核心机制
- **流式**：SSE 逐块解析，Ctrl+C 中断并保留已生成部分
- **思考档位**：off/low/medium/high → `reasoning_effort`，provider 配置 `thinking_extra` 可覆盖
- **图片**：Pillow 压缩 1568px JPEG data URL，多图；直接粘贴路径即发送
- **上下文压缩**：超过 `compress_threshold × context_window` 时把最早的可压缩消息压缩为摘要（调模型生成），仍超窗则紧急截断；摘要落库持久化
- **补全**：命令名补全；`/model` 模型列表、会话类命令会话 id、`/think` 档位、`/attach` 路径

## 阶段
1. Phase 0 脚手架：pyproject、包结构、config 示例
2. Phase 1 核心对话：配置、provider、流式 REPL、Ctrl+C 中断
3. Phase 2 持久化+补全框架：SQLite、会话命令、命令名补全
4. Phase 3 记录复用：/fork /load
5. Phase 4 图片：编码、/attach、路径识别
6. Phase 5 思考强度+模型补全
7. Phase 6 上下文管理：估算、截断、hidden
8. Phase 7 上下文压缩：摘要生成、持久化、/compress

> 注：因需求收紧（去掉导入导出/Anthropic、极致简化），以上阶段已合并到一次实现中完成。
