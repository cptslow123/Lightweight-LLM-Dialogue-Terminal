# Light Harness

本项目有两个独立形态：

- `cli/`：Python 命令行版本。运行 `cli\start.bat`；首次使用运行 `cli\install.bat`。
- `gui/`：Tauri 桌面版本。运行 `gui\dev.bat` 进行开发；可直接分发的应用位于 `gui\release/`。

根目录 `.venv/` 是两者共享的 Python 环境。各版本的配置与聊天数据互相独立：CLI 使用其自身数据目录，GUI 开发数据位于 `gui\data/`，桌面发布版的数据与 EXE 保持同级。
