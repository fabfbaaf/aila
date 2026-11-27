"""启动服务：python -m app（安装包里是 aila-ai.exe，入口一样）

    python -m app                       启动 AI 服务
    python -m app --download-models     下载语音模型和向量模型（后面可以跟类别，比如 tts-kokoro）
    python -m app --self-check          打包后的自检：各个库和它们自带的数据文件都能加载
"""

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--download-models"]:
        from .models_download import main as download

        return download(args[1:])
    if args[:1] == ["--self-check"]:
        from .selfcheck import main as check

        return check()
    if args[:1] == ["--claude-quota"]:
        from .runtime.claude_statusline import main as collect

        return collect(args[1:])

    from . import winsock

    winsock.install()  # Windows 上建事件循环偶尔卡死，见 winsock.py
    import uvicorn

    from .config import load_settings

    s = load_settings()
    # 工作窗口、终端订阅着正在做的任务（长连接）：关服务时最多等 3 秒，然后打断任务、断开连接
    uvicorn.run("app.main:app", host=s.host, port=s.port, timeout_graceful_shutdown=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
