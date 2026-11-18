"""数据库连接。"""

import os

DEFAULT_URL = "sqlite:///local.db"


def database_url_from_env():
    return os.environ.get("DATABASE_URL") or DEFAULT_URL


def connect():
    url = database_url_from_env()
    return {"url": url}
