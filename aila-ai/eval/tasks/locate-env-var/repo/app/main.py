"""入口。"""

from app.db.connection import connect


def start():
    return connect()
