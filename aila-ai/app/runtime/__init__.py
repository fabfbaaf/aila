"""编程 Agent 运行时：给她一个任务，她在你的项目里读代码、改文件、跑命令和测试，直到做完。

设计参考了 DeepSeek Harness（会话事件日志、Agent 循环上的钩子、一个核心多个入口），用 Python 自己实现：

- session.py      会话 = 只追加的事件日志（data/sessions/<id>.jsonl），发给模型的消息由它推出来
- prompt.py       固定的系统提示词 + 会话开头的环境说明（日期、项目、AGENTS.md / CLAUDE.md），开头不变才能命中缓存
- workspace.py    项目目录：路径检查（不出项目、不碰 .git）、读过才能改、密钥文件要先问
- tools.py        编码工具：读文件、找文件、搜内容、改文件、写文件、跑命令、待办清单
- shell.py        跑命令：Git Bash（Windows 上有的话）或 PowerShell，超时杀整棵进程树，输出截断，去掉自己的密钥
- permissions.py  权限：只读 / 先问再改 / 工作区内自动，危险命令始终要问，按项目记住允许过的命令
- loop.py         Agent 循环：一步 = 一次模型请求 + 它要的工具；钩子、用量、打断、上下文压缩
- runner.py       会话在后台跑，桌面和终端都能订阅同一个会话的事件
- cache.py        估算前缀缓存命中（假模型和测试用；真模型用服务端返回的数字）
"""
