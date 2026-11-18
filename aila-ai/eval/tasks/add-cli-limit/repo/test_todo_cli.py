from todo_cli import main


def run(argv):
    lines = []
    main(argv, lines.append)
    return lines


def test_done_filter():
    assert run(["--done"]) == ["写周报", "回邮件"]


def test_limit():
    assert run(["--limit", "2"]) == ["写周报", "修登录 bug"]
