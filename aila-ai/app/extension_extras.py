"""扩展包里给桌面伙伴的东西（第二期）和扩展自带的页面（第三期）：读出来、检查、限住范围。

    motions/<名字>.json      一段动作：和内置的点头、挥手同样的格式（几段姿态，每段多久、头和手到什么程度、带什么表情）
    lines.json               台词：往她各种场合的台词池里加句子（被戳、被拖、刚醒……），也可以整组换掉
    avatars/<名字>.vrm       形象：在设置的「形象」里能换上
    pages/<名字>.html        页面：在扩展页点「打开」，在沙盒窗口里显示（不能联网、碰不到本机服务）

为什么动作只能动这些「姿态值」而不是直接转骨骼：值的范围由代码限住（抬手最多抬到 1、头最多歪多少），
换哪个模型都不会把她扭成奇怪的样子；也和内置动作走同一条路（Behavior 的 Phase）。
动作名在对话里也能用（[名字]）：开对话时系统提示词里列出扩展的动作，标签解析认这些名字。
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 姿态值的范围（和 aila-desktop 的 PoseState 对应）。超出的截到边上
POSE_LIMITS: dict[str, tuple[float, float]] = {
    "headPitch": (-0.6, 0.6), "headYaw": (-0.8, 0.8), "headRoll": (-0.5, 0.5),
    "bodyLean": (-0.3, 0.3), "bodySway": (-0.3, 0.3),
    "armRaise": (0.0, 1.0), "forearmSwing": (-1.0, 1.0), "armsUp": (0.0, 1.0),
}
EXPR_KEYS = ("happy", "sad", "surprised", "relaxed", "blink")
# 内置的动作、表情和别的标签名：扩展的动作不能和它们重名
RESERVED = {"nod", "shake", "tilt", "think", "bow", "wave", "stretch", "say", "neutral", *EXPR_KEYS}
MOTION_NAME = re.compile(r"^[a-z][a-z0-9_]{1,19}$")
MAX_PHASES = 40
MAX_MOTION_SECONDS = 10.0
MAX_MOTIONS = 30

# 台词池（aila-desktop/src/renderer/src/persona/lines.ts 里是句子列表的那些）
LINE_KEYS = (
    "chatOpen", "pokeHead", "pokeHeadMany", "pokeBody", "dragStart", "dragEnd", "wake", "sleepy", "wakeHere",
    "forgetOne", "agentStart", "agentTaskStarted", "testsRecovered", "testsStruggling", "workMaking", "lateNight",
)
MAX_LINES_PER_KEY = 30
MAX_LINE_CHARS = 80

PAGE_NAME = re.compile(r"^[\w一-鿿-]{1,40}$")
AVATAR_NAME = PAGE_NAME
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_AVATAR_BYTES = 100 * 1024 * 1024
TITLE = re.compile(r"<title>(.*?)</title>", re.I | re.S)


class ExtrasError(Exception):
    """动作、台词、页面写得不对（导入时报给你看，装好以后坏掉的那个跳过）"""


@dataclass(frozen=True)
class Motion:
    name: str
    label: str
    phases: list[dict[str, Any]]


def parse_motion(name: str, raw: Any) -> Motion:
    if not MOTION_NAME.fullmatch(name) or name in RESERVED:
        raise ExtrasError(f"动作名「{name}」不行：小写字母开头，字母数字下划线，2–20 个字，不能和内置的动作、表情重名。")
    if not isinstance(raw, dict) or not isinstance(raw.get("phases"), list) or not raw["phases"]:
        raise ExtrasError(f"动作 {name} 要有 phases（几段姿态）。")
    if len(raw["phases"]) > MAX_PHASES:
        raise ExtrasError(f"动作 {name} 太长了（最多 {MAX_PHASES} 段）。")
    phases: list[dict[str, Any]] = []
    total = 0.0
    for i, p in enumerate(raw["phases"], 1):
        if not isinstance(p, dict):
            raise ExtrasError(f"动作 {name} 的第 {i} 段要是一个对象。")
        dur = p.get("dur")
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or not 0.05 <= dur <= 5:
            raise ExtrasError(f"动作 {name} 的第 {i} 段：dur 要是 0.05 到 5 秒。")
        total += float(dur)
        phase: dict[str, Any] = {"dur": float(dur)}
        for group, allowed in (("pose", POSE_LIMITS), ("expr", {k: (0.0, 1.0) for k in EXPR_KEYS})):
            values = p.get(group, {})
            if not isinstance(values, dict):
                raise ExtrasError(f"动作 {name} 的第 {i} 段：{group} 要是一个对象。")
            clean: dict[str, float] = {}
            for key, value in values.items():
                if key not in allowed:
                    raise ExtrasError(f"动作 {name} 的第 {i} 段：不认识 {group}.{key}（能用：{'、'.join(allowed)}）。")
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ExtrasError(f"动作 {name} 的第 {i} 段：{group}.{key} 要是数字。")
                lo, hi = allowed[key]
                clean[key] = round(min(hi, max(lo, float(value))), 4)
            if clean:
                phase[group] = clean
        phases.append(phase)
    if total > MAX_MOTION_SECONDS:
        raise ExtrasError(f"动作 {name} 太长了（加起来最多 {MAX_MOTION_SECONDS:.0f} 秒）。")
    label = raw.get("label", name)
    if not isinstance(label, str) or not label.strip():
        label = name
    return Motion(name, label.strip()[:20], phases)


def scan_motions(folder: Path, strict: bool = False) -> list[Motion]:
    """motions/*.json。strict：导入时有一个不对就报错；装好以后坏掉的跳过"""
    root = folder / "motions"
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.json"))[:MAX_MOTIONS]:
        try:
            out.append(parse_motion(path.stem, json.loads(path.read_text(encoding="utf-8-sig"))))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
            if strict:
                raise ExtrasError(f"motions/{path.name} 读不了：{err}") from err
        except ExtrasError:
            if strict:
                raise
    return out


def parse_lines(raw: Any) -> tuple[dict[str, list[str]], bool]:
    """lines.json：{"replace": false, "lines": {"pokeHead": ["……"]}} → （台词，是否整组换掉）"""
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), dict):
        raise ExtrasError("lines.json 要写成 {\"lines\": {\"pokeHead\": [\"……\"]}}。")
    out: dict[str, list[str]] = {}
    for key, items in raw["lines"].items():
        if key not in LINE_KEYS:
            raise ExtrasError(f"lines.json 里的 {key} 不认识（能用：{'、'.join(LINE_KEYS)}）。")
        if not isinstance(items, list) or not all(isinstance(t, str) for t in items):
            raise ExtrasError(f"lines.json 里的 {key} 要是句子列表。")
        clean = ["".join(ch for ch in t if ch.isprintable()).strip()[:MAX_LINE_CHARS] for t in items[:MAX_LINES_PER_KEY]]
        clean = [t for t in clean if t]
        if clean:
            out[key] = clean
    return out, raw.get("replace") is True


def read_lines(folder: Path, strict: bool = False) -> tuple[dict[str, list[str]], bool]:
    path = folder / "lines.json"
    if not path.is_file():
        return {}, False
    try:
        return parse_lines(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ExtrasError) as err:
        if strict:
            raise ExtrasError(str(err)) from err
        return {}, False


def scan_avatars(folder: Path) -> list[dict[str, Any]]:
    root = folder / "avatars"
    if not root.is_dir():
        return []
    return [{"name": p.stem, "file": str(p), "size": p.stat().st_size}
            for p in sorted(root.glob("*.vrm")) if p.is_file() and AVATAR_NAME.fullmatch(p.stem)
            and p.stat().st_size <= MAX_AVATAR_BYTES]


def scan_pages(folder: Path) -> list[dict[str, str]]:
    root = folder / "pages"
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*.html")):
        if not p.is_file() or not PAGE_NAME.fullmatch(p.stem) or p.stat().st_size > MAX_PAGE_BYTES:
            continue
        try:
            head = p.read_text(encoding="utf-8-sig", errors="replace")[:4000]
        except OSError:
            continue
        m = TITLE.search(head)
        title = " ".join(m.group(1).split())[:40] if m else ""
        out.append({"name": p.stem, "title": title or p.stem})
    return out


def page_file(folder: Path, name: str) -> Path:
    if not PAGE_NAME.fullmatch(name):
        raise ExtrasError("页面名不对。")
    path = folder / "pages" / f"{name}.html"
    if not path.is_file() or path.stat().st_size > MAX_PAGE_BYTES:
        raise ExtrasError(f"没有 {name} 这个页面。")
    return path


def extras_summary(folder: Path, strict: bool = False) -> dict[str, Any]:
    """装之前的清单、扩展页用：动作、台词、形象、页面"""
    lines, replace = read_lines(folder, strict)
    return {
        "motions": [{"name": m.name, "label": m.label} for m in scan_motions(folder, strict)],
        "lines": {k: len(v) for k, v in lines.items()},
        "linesReplace": replace,
        "avatars": [{"name": a["name"], "size": a["size"]} for a in scan_avatars(folder)],
        "pages": scan_pages(folder),
    }


def motions_prompt(motions: list[Motion]) -> str:
    """接在对话系统提示词后面：扩展带来的动作也能用。没有就是空串（系统提示词和以前一字不差）"""
    if not motions:
        return ""
    names = " ".join(f"[{m.name}] {m.label}" for m in motions)
    return f"\n\n扩展带来的动作（和上面的动作标签一样用，放在 [say] 前面）：{names}"
