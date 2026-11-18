import pytest
from todo_cli import main


def run(argv):
    lines = []
    main(argv, lines.append)
    return lines


def test_limit_after_filter():
    assert run(["--done", "--limit", "1"]) == ["写周报"]
    assert run(["--limit", "10"]) == ["写周报", "修登录 bug", "回邮件", "买咖啡豆"]


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_bad_limit(bad):
    with pytest.raises(SystemExit) as err:
        run(["--limit", bad])
    assert err.value.code == 2
