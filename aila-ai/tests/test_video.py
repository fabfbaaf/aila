"""内置视频由固定命令检查、渲染、验收；路径受工作区限制，已有成品会保留。"""

from pathlib import Path

import pytest

from app.runtime import video
from app.runtime.permissions import Policy, Rules
from app.runtime.shell import CommandResult
from app.runtime.workspace import Workspace


@pytest.mark.anyio
async def test_render_video_publishes_verified_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "video"
    project.mkdir()
    (project / "index.html").write_text("<main>样片</main>", encoding="utf-8")
    runtime = video.VideoRuntime(tmp_path / "cache")
    steps: list[str] = []

    async def command():
        return ["hyperframes"], "ffprobe", {}

    async def run(argv, cwd, timeout, *, env):
        if argv[0] == "ffprobe":
            steps.append("probe")
            return CommandResult(0, '{"streams":[{"codec_type":"video","width":1280,"height":720}],'
                                   '"format":{"duration":"3"}}', 0, False)
        steps.append(argv[1])
        if argv[1] == "render":
            Path(argv[-1]).write_bytes(b"fixture")
        return CommandResult(0, "", 0, False)

    monkeypatch.setattr(runtime, "_command", command)
    monkeypatch.setattr(video, "run_argv", run)
    tool = runtime.tool(Workspace(project))
    rules = Rules(tmp_path / "rules.json")
    assert Policy(project, rules, "read_only").decide(tool.kind({})).verdict == "deny"
    assert Policy(project, rules, "full").decide(tool.kind({})).verdict == "ask"

    assert not (await tool.run({"project": ".", "output": "../escape.mp4"})).ok
    args = {"project": ".", "output": "clip.mp4"}
    outcome = await tool.run(args)
    assert outcome.ok and "3.00 秒" in outcome.content
    assert (project / "clip.mp4").read_bytes() == b"fixture"
    assert not (await tool.run(args)).ok
    assert steps == ["check", "render", "probe"]
