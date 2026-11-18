"""列出待办事项。"""

import argparse

TODOS = [
    {"title": "写周报", "done": True},
    {"title": "修登录 bug", "done": False},
    {"title": "回邮件", "done": True},
    {"title": "买咖啡豆", "done": False},
]


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(prog="todo")
    parser.add_argument("--done", action="store_true", help="只看做完的")
    args = parser.parse_args(argv)
    items = [t for t in TODOS if t["done"]] if args.done else TODOS
    for t in items:
        out(t["title"])
    return 0
