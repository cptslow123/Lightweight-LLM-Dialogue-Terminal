# Light Harness GUI

这是一个 Tauri 独立窗口 GUI，界面在 Windows WebView2 中运行，不会打开浏览器。

双击 dev.bat 启动开发窗口。Tauri 会自动启动本地 Python 后端。

双击 build.bat 构建便携版，产物位于 release 文件夹：Light Harness.exe 为桌面窗口，backend.exe 为随程序携带的 Python 后端。

运行时 config.toml 和 chat.db 均位于 Light Harness.exe 同级目录。复制整个 release 文件夹即可迁移模型、Provider、Firecrawl（fc）设置和所有会话；最终用户不需要安装 Python。
