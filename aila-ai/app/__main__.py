"""启动服务：python -m app"""

import uvicorn

from .config import load_settings

if __name__ == "__main__":
    s = load_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port)
