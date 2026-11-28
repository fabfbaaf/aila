"""验收和交付：一个任务做完以后，在一张交付卡上看清它做了什么，再决定「通过」还是「退回修改」。

- 验收记录：任务日志里一种事件 {"type": "acceptance", "verdict": "accepted" | "rejected", "note", "comments"}，
  任务摘要带着最新的结论。以后「子任务交回主任务」「从验收过的阶段恢复」「前置任务验收后自动开工」都认这一种，不各记各的
- 交付卡的内容全部由代码算，不让她自己说：
  - 改了哪些文件、每个文件的 diff：在新分支上做的和分支开出来时比（她就在那个临时目录里改）；
    直接在项目里做的只看这个任务改过的文件（你自己同时改的别的文件不算进来）
  - 最后一次跑测试：日志里最后一条像测试的命令（pytest、vitest、npm test……）和它的结果
  - PR：日志里最后出现的 GitHub PR 链接；CI：有 gh 就用 gh 问，没有就问 GitHub 的公开接口（私有仓库问不到就说问不到）
- 退回修改：你在 diff 上点某一行写的意见，加上一句总的，由代码拼成一段话交给她，接着在这个任务里改
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from ..code.fix import NO_WINDOW

KIND = "acceptance"
MAX_FILES = 40
MAX_DIFF_LINES = 400
MAX_COMMENTS = 50
TAIL_CHARS = 1500

# 和桌面端 shared/agent.ts 的 isTestCommand 一样：常见的测试命令
TEST_COMMAND = re.compile(
    r"(^|[\s;&|/\\])(pytest|vitest|jest|mocha)(\s|$)|-m\s+pytest|\b(npm|pnpm|yarn)(\.cmd)?\s+(run\s+)?test\b|"
    r"\bnpx(\.cmd)?\s+(vitest|jest)\b|mvnw?(\.cmd)?\s+(-\S+\s+)*test\b|\bgradlew?\s+test\b|\b(go|cargo|dotnet)\s+test\b|"
    r"\bnode\s+--test\b",
    re.I,
)
PR_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")


# ---------- 验收记录 ----------


def latest(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """最新一次验收（没验收过是 None）"""
    found = None
    for e in events:
        if e.get("type") == KIND:
            found = e
    return found


def verdict_of(events: Iterable[dict[str, Any]]) -> str | None:
    """任务摘要用：accepted / rejected / None。验收之后又做了新的一轮，就不算验收过了（要重新看）"""
    last_accept, last_turn = -1, -1
    verdict = None
    for i, e in enumerate(events):
        if e.get("type") == KIND:
            last_accept, verdict = i, e.get("verdict")
        elif e.get("type") == "turn_end":
            last_turn = i
    if verdict is None or last_turn > last_accept:
        return None
    return str(verdict)


def clean_comments(raw: Any) -> list[dict[str, Any]]:
    """页面传来的行内意见：只留文件、行号、哪一边（新 / 旧）、内容，长度挡一下"""
    out = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        file, text = str(c.get("file", "")).strip()[:500], str(c.get("text", "")).strip()[:2000]
        line = c.get("line")
        if not file or not text or not isinstance(line, int) or line < 1:
            continue
        out.append({"file": file, "line": line, "side": "old" if c.get("side") == "old" else "new", "text": text})
    return out[:MAX_COMMENTS]


def rejection_text(note: str, comments: list[dict[str, Any]]) -> str:
    """退回修改时交给她的话（代码拼的）：总的意见在前，再一条条列出行内意见"""
    lines = ["这次的改动验收没通过，照下面的意见改，改完重新跑一遍测试，再告诉我改了什么。"]
    if note.strip():
        lines.append(f"总的意见：{note.strip()}")
    if comments:
        lines.append("具体到行的意见：")
        for c in comments:
            where = f"{c['file']} 第 {c['line']} 行" + ("（删掉的那行）" if c["side"] == "old" else "")
            lines.append(f"- {where}：{c['text']}")
    return "\n".join(lines)


# ---------- 交付卡 ----------


def _git(root: Path, *args: str, timeout: float = 20) -> str | None:
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _calls(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """工具调用编号 → 名字和参数（她自己的、本机 Agent 的都记在 assistant 事件的 tool_calls 里）"""
    out: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("type") == "assistant":
            for c in e.get("tool_calls") or []:
                try:
                    args = json.loads(c.get("arguments") or "{}")
                except ValueError:
                    args = {}
                out[str(c.get("id"))] = {"name": c.get("name"), "args": args if isinstance(args, dict) else {}}
    return out


def touched_files(events: list[dict[str, Any]]) -> list[str]:
    """这个任务改过的文件（改成功的那些，按第一次改的顺序）"""
    out: list[str] = []
    for e in events:
        if e.get("type") == "tool" and e.get("status") == "ok" and e.get("file") and e["file"] not in out:
            out.append(str(e["file"]).replace("\\", "/"))
    return out


def last_tests(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最后一次跑测试：命令、过没过、输出的最后一段"""
    calls = _calls(events)
    found = None
    for e in events:
        if e.get("type") != "tool" or e.get("name") != "run_command":
            continue
        command = str(calls.get(str(e.get("id")), {}).get("args", {}).get("command", ""))
        if command and TEST_COMMAND.search(command):
            content = str(e.get("content", ""))
            # 结果的 status 按退出码来：0 是 ok
            found = {"command": command[:300], "ok": e.get("status") == "ok", "tail": content[-TAIL_CHARS:], "seq": e.get("seq")}
    return found


def pr_of(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """日志里最后出现的 GitHub PR 链接（她开 PR 时 gh 会打出来）"""
    found = None
    for e in events:
        for text in (e.get("content"), e.get("text")):
            if isinstance(text, str):
                for m in PR_URL.finditer(text):
                    found = {"url": m.group(0), "owner": m.group(1), "repo": m.group(2), "number": int(m.group(3))}
    return found


def changes(root: Path, events: list[dict[str, Any]], branch: dict[str, Any] | None, branch_gone: bool = False) -> dict[str, Any]:
    """改了哪些文件和 diff。在分支上做的：整个临时目录和分支起点比；直接在项目里做的：只看这个任务改过的文件"""
    touched = touched_files(events)
    listed = {"files": [{"path": f, "added": None, "deleted": None, "diff": "", "untracked": False} for f in touched[:MAX_FILES]],
              "base": None}
    if branch_gone:
        return {**listed, "note": "分支已经合并或放弃了：只列出改过的文件，diff 去项目的提交记录里看。"}
    if _git(root, "rev-parse", "--git-dir") is None:
        return {**listed, "note": "这个项目不是 git 仓库：只列出改过的文件，看不了 diff。"}
    base = str(branch.get("base")) if branch and branch.get("base") else "HEAD"
    only = None if branch else touched
    if only is not None and not only:
        return {"files": [], "base": base, "note": "这个任务还没改过文件。"}
    scope = ["--", *only] if only else ["--", "."]
    # --relative：路径相对她干活的目录（和改文件时记的路径一样），也只看这个目录里的
    numstat = _git(root, "diff", "--numstat", "--relative", base, *scope) or ""
    files: list[dict[str, Any]] = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added, deleted, path = parts
            files.append({"path": path, "added": int(added) if added.isdigit() else None,
                          "deleted": int(deleted) if deleted.isdigit() else None, "diff": "", "untracked": False})
    known = {f["path"] for f in files}
    # 新建、还没 git add 的文件：git diff 看不到，按全是新加的行算
    for path in (_git(root, "ls-files", "--others", "--exclude-standard", *scope) or "").splitlines():
        path = path.strip()
        if path and path not in known:
            files.append({"path": path, "added": None, "deleted": 0, "diff": "", "untracked": True})
    for f in files[:MAX_FILES]:
        if f["untracked"]:
            f["diff"], f["added"] = _new_file_diff(root, f["path"])
        else:
            f["diff"] = _cap(_git(root, "diff", "--no-color", "--no-ext-diff", "--relative", base, "--", f["path"]) or "")
    note = "" if len(files) <= MAX_FILES else f"改了 {len(files)} 个文件，只列出前 {MAX_FILES} 个。"
    return {"files": files[:MAX_FILES], "base": base, "note": note}


def _new_file_diff(root: Path, path: str) -> tuple[str, int | None]:
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        return "", None
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", None
    body = "\n".join("+" + line for line in lines[:MAX_DIFF_LINES])
    more = f"\n… 还有 {len(lines) - MAX_DIFF_LINES} 行没显示" if len(lines) > MAX_DIFF_LINES else ""
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}{more}", len(lines)


def _cap(diff: str) -> str:
    lines = diff.splitlines()
    if len(lines) <= MAX_DIFF_LINES:
        return diff
    return "\n".join(lines[:MAX_DIFF_LINES]) + f"\n… 还有 {len(lines) - MAX_DIFF_LINES} 行没显示"


def ci_status(pr: dict[str, Any] | None, root: Path, fetch: Any = None) -> dict[str, Any] | None:
    """PR 上的 CI：有 gh 用 gh（私有仓库也行）；没有就问 GitHub 的公开接口（只看得到公开仓库）"""
    if pr is None:
        return None
    if shutil.which("gh"):
        try:
            p = subprocess.run(["gh", "pr", "checks", pr["url"], "--json", "name,state,bucket,link"], cwd=root,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                               creationflags=NO_WINDOW)
            if p.returncode in (0, 8) and p.stdout.strip():  # 8：还有没跑完的
                checks = [{"name": c.get("name", ""), "state": _bucket(c.get("bucket"), c.get("state")), "link": c.get("link", "")}
                          for c in json.loads(p.stdout)]
                return {"state": _overall(checks), "checks": checks, "source": "gh"}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    fetch = fetch or _fetch_json
    try:
        info = fetch(f"https://api.github.com/repos/{pr['owner']}/{pr['repo']}/pulls/{pr['number']}")
        sha = info["head"]["sha"]
        runs = fetch(f"https://api.github.com/repos/{pr['owner']}/{pr['repo']}/commits/{sha}/check-runs")
    except Exception:
        return {"state": "unknown", "checks": [], "source": "api",
                "note": "问不到 CI：私有仓库要先装 gh 并登录（gh auth login），或者网络不通"}
    checks = [{"name": r.get("name", ""), "state": _bucket(None, r.get("status"), r.get("conclusion")), "link": r.get("html_url", "")}
              for r in runs.get("check_runs", [])]
    return {"state": _overall(checks), "checks": checks, "source": "api", "merged": bool(info.get("merged")),
            "open": info.get("state") == "open"}


def _bucket(bucket: Any, state: Any = None, conclusion: Any = None) -> str:
    """各种写法统一成 pass / fail / pending / skip"""
    for value in (bucket, conclusion, state):
        v = str(value or "").lower()
        if v in ("pass", "success", "neutral"):
            return "pass"
        if v in ("fail", "failure", "timed_out", "cancelled", "action_required", "startup_failure", "error"):
            return "fail"
        if v in ("skipping", "skipped"):
            return "skip"
    return "pending"


def _overall(checks: list[dict[str, Any]]) -> str:
    states = {c["state"] for c in checks}
    if not checks:
        return "none"
    if "fail" in states:
        return "fail"
    if "pending" in states:
        return "pending"
    return "pass"


def _fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "aila"})
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 只请求固定的 api.github.com
        return json.load(r)


def card(root: Path, events: list[dict[str, Any]], branch: dict[str, Any] | None, branch_gone: bool = False,
         with_ci: bool = True, fetch: Any = None) -> dict[str, Any]:
    """交付卡：改动、测试、分支、PR、CI、验收结论（全部代码算）"""
    pr = pr_of(events)
    return {
        "changes": changes(root, events, branch, branch_gone),
        "tests": last_tests(events),
        "branch": branch.get("branch") if branch else None,
        "pr": pr,
        "ci": ci_status(pr, root, fetch) if with_ci else None,
        "acceptance": latest(events),
        "verdict": verdict_of(events),
    }


__all__ = ["KIND", "card", "changes", "ci_status", "clean_comments", "last_tests", "latest", "pr_of", "rejection_text",
           "touched_files", "verdict_of"]
