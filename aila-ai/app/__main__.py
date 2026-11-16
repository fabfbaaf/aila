"""启动服务：python -m app"""

import uvicorn

from .config import load_settings

if __name__ == "__main__":
    s = load_settings()
    # 工作窗口、终端订阅着正在做的任务（长连接）：关服务时最多等 3 秒，然后打断任务、断开连接
    uvicorn.run("app.main:app", host=s.host, port=s.port, timeout_graceful_shutdown=3)
