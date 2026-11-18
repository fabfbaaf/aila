"""数据库。"""

DB = []


def save_order(user, items):
    DB.append({"user": user, "items": items})
    return len(DB)
