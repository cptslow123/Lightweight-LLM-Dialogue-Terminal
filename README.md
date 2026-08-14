# llm-harness

轻量终端 LLM harness：流式对话、思考强度、图片上传、多会话管理、上下文压缩。仅支持 OpenAI 兼容协议（DeepSeek / Kimi / GLM / Ollama / vLLM / LM Studio 等）。

## 安装
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## 配置
```powershell
New-Item -ItemType Directory -Force $env:USERPROFILE\.llm_harness
Copy-Item config.example.toml $env:USERPROFILE\.llm_harness\config.toml
# 编辑模型、base_url；key 二选一：
#   1. 环境变量：api_key_env 指向变量名，如 $env:DEEPSEEK_API_KEY = "sk-..."
#   2. 配置文件明文：provider 里加 api_key = "sk-..."（/setting 自动写入）
```
程序内运行 `/setting` 会新开一个终端窗口，交互式配置 base_url / api_key / 模型，完成后 `/reload` 生效。base_url 需包含 `/v1`，程序自动追加 `/chat/completions`。

## 运行
```powershell
python -m llm_harness
# 可选：--provider / --model / --think / --config
```

## 命令
`/new` 新建 · `/list` 列表 · `/switch <id>` 切换 · `/rename` `/delete` `/clear`
`/fork` 分支 · `/load <id> [N]` 拼接历史 · `/model` `/think` 切换
`/attach <路径>` 或直接把文件拖入终端发送 · `/compress` 手动压缩 · `/usage` 占用
`/setting` 新窗口配置向导 · `/reload` 重载配置

生成中 Ctrl+C 中断；输入中 Ctrl+C 清空。

## 附件
- 图片（png/jpg/jpeg/webp/gif）：压缩为 1568px JPEG 后按多模态发送。
- 文本文件（txt/md/csv/json/py 等）：直接读入作为文本。
- PDF/docx/xlsx：本地提取文字后注入上下文，需安装可选依赖：`pip install -e ".[files]"`（或运行 install.bat 自动安装）。
- 使用方式：直接拖入终端、粘贴路径，或 `/attach <路径...>`；路径含空格时自动带引号即可。

## 测试
```powershell
.\.venv\Scripts\python tests\smoke.py   # mock 服务器端到端冒烟
```
