"""扩展包：一个文件夹或 zip，导入一次，里面的命令、本事、外部工具一起装上，也能一起停用、卸掉。

一个扩展包长这样：

    jira-helper/
      aila-extension.json       说明书（必须有）
      commands/*.md             新的 /命令（和项目里 .aila/commands/ 的写法一样）
      skills/<名字>/SKILL.md    一项「本事」：开头 --- 包起来的 description 是一句话简介，下面是全文
      server/...                外部工具服务（MCP），在说明书的 mcpServers 里写怎么启动
      motions/ lines.json avatars/ pages/   桌面伙伴的动作、台词、形象和自带页面（见 extension_extras.py）

从目录（catalog.json）浏览、下载见 extension_catalog.py。

说明书：
    {
      "id": "jira-helper", "name": "Jira 助手", "version": "1.0.0", "aila": ">=0.1",
      "description": "一句话",
      "requires": ["node"],                                  # 要电脑上装好的程序（只提示，不替你装）
      "mcpServers": {"jira": {"command": "node", "args": ["${extension}/server/index.js"],
                              "env": {"JIRA_TOKEN": "${setting:token}"}, "readOnly": false}},
      "settings": [{"key": "token", "label": "Jira 令牌", "secret": true, "required": true}]
    }

为什么这样设计：
- 扩展的代码只在单独的进程里跑（MCP 服务），不往 AI 服务里加载别人写的 Python：
  现有的权限规则全部照旧（外部工具只读的直接跑、别的每次问，危险命令任何模式都问，扩展改不了规则、读不到 .env）
- 装之前给你看的清单（会加哪些命令、本事、工具，会运行什么程序，要填什么）由代码从说明书算出来，
  不用扩展自己写的介绍；你点了「装」才装。装好以后它的工具服务默认是停的，你自己打开
- 装在 <数据目录>/extensions/<id>/，开关、设置、「以后都不用问」记在同一个目录的 state.json；
  卸载就是删掉那个文件夹。设置里的密钥只作为环境变量传给它自己的服务，不回传给界面、不进对话
- 正在进行的会话不受影响：命令每次现读，本事和工具在新会话（新的一轮）里生效，前缀缓存不断
- 解压时挡掉 ../、绝对路径、链接，限制文件数和大小
"""

import hashlib
import json
import re
import secrets
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .extension_extras import (
    ExtrasError,
    Motion,
    extras_summary,
    page_file,
    read_lines,
    scan_avatars,
    scan_motions,
)

# 艾拉自己的版本：扩展在说明书里写「aila」: ">=0.1" 要求最低版本
AILA_VERSION = "0.1.0"
MANIFEST = "aila-extension.json"
STATE = "state.json"
STAGING = ".staging"

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
SERVER = re.compile(r"^[A-Za-z0-9_]{1,7}$")  # 拼成「扩展id-服务名」后不超过 MCP 服务名的 32 个字
SETTING_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
SKILL_NAME = re.compile(r"^[\w一-鿿-]{1,40}$")
VERSION = re.compile(r"^\d+(\.\d+){0,2}$")
PLACEHOLDER = re.compile(r"\$\{setting:([A-Za-z][A-Za-z0-9_]{0,31})\}")
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

MAX_FILES = 2000
MAX_TOTAL = 200 * 1024 * 1024
MAX_FILE = 100 * 1024 * 1024
MAX_SKILLS = 50
MAX_SKILL_CHARS = 30_000
MAX_SETTING_CHARS = 2000


class ExtensionError(Exception):
    """说明书不对、包有问题、找不到扩展"""


# ---------- 说明书 ----------


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    secret: bool = False
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    folder: Path
    extension: str  # 扩展 id
    extension_name: str


@dataclass
class Manifest:
    id: str
    name: str
    version: str
    description: str
    aila: str
    requires: list[str]
    servers: dict[str, dict[str, Any]]
    settings: list[SettingSpec]


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = [int(p) for p in text.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def version_ok(requirement: str, current: str = AILA_VERSION) -> bool:
    """只认「>=x.y.z」和空：扩展说自己要的最低版本"""
    req = requirement.strip()
    if not req:
        return True
    if not req.startswith(">=") or not VERSION.fullmatch(req[2:].strip()):
        raise ExtensionError(f"「aila」只能写成 >=版本号（比如 >=0.1），现在是 {requirement!r}。")
    return _version_tuple(current) >= _version_tuple(req[2:].strip())


def _text(raw: dict[str, Any], key: str, limit: int, required: bool = False) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ExtensionError(f"说明书里的 {key} 要是文字。")
    value = value.strip()
    if required and not value:
        raise ExtensionError(f"说明书里缺 {key}。")
    if len(value) > limit:
        raise ExtensionError(f"说明书里的 {key} 太长了（最多 {limit} 个字）。")
    return value


def parse_manifest(raw: Any) -> Manifest:
    if not isinstance(raw, dict):
        raise ExtensionError("说明书要是一个 JSON 对象。")
    ext_id = _text(raw, "id", 24, required=True)
    if not ID.fullmatch(ext_id):
        raise ExtensionError(f"扩展 id「{ext_id}」不行：小写字母、数字、减号，最多 24 个字。")
    version = _text(raw, "version", 20, required=True)
    if not VERSION.fullmatch(version):
        raise ExtensionError(f"版本号「{version}」不行：写成 1.0.0 这样。")
    requires = raw.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) and re.fullmatch(r"[\w.-]{1,40}", r) for r in requires):
        raise ExtensionError("requires 要是程序名的列表，比如 [\"node\"]。")

    settings: list[SettingSpec] = []
    raw_settings = raw.get("settings", [])
    if not isinstance(raw_settings, list):
        raise ExtensionError("settings 要是一个列表。")
    for item in raw_settings:
        if not isinstance(item, dict) or not SETTING_KEY.fullmatch(str(item.get("key", ""))):
            raise ExtensionError("settings 里每一项要有 key（字母开头，字母数字下划线）。")
        key = str(item["key"])
        if any(s.key == key for s in settings):
            raise ExtensionError(f"settings 里的 {key} 重复了。")
        settings.append(SettingSpec(key, _text(item, "label", 40) or key, item.get("secret") is True,
                                    item.get("required") is True, _text(item, "description", 200)))

    servers: dict[str, dict[str, Any]] = {}
    raw_servers = raw.get("mcpServers", {})
    if not isinstance(raw_servers, dict):
        raise ExtensionError("mcpServers 要是「名字: 配置」。")
    keys = {s.key for s in settings}
    for name, cfg in raw_servers.items():
        if not SERVER.fullmatch(name):
            raise ExtensionError(f"工具服务名「{name}」不行：字母、数字、下划线，最多 7 个字。")
        if not isinstance(cfg, dict) or not isinstance(cfg.get("command"), str) or not cfg["command"].strip():
            raise ExtensionError(f"工具服务 {name} 要有 command（在本机启动的程序）。扩展里不支持远程服务。")
        args = cfg.get("args", [])
        env = cfg.get("env", {})
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ExtensionError(f"工具服务 {name} 的 args 要是文字列表。")
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ExtensionError(f"工具服务 {name} 的 env 要是「名字: 文字」。")
        for text in [cfg["command"], *args, *env.values()]:
            for used in PLACEHOLDER.findall(text):
                if used not in keys:
                    raise ExtensionError(f"工具服务 {name} 用到了没在 settings 里声明的设置 {used}。")
        servers[name] = {"command": cfg["command"].strip(), "args": list(args), "env": dict(env),
                         "readOnly": cfg.get("readOnly") is True}

    return Manifest(ext_id, _text(raw, "name", 40) or ext_id, version, _text(raw, "description", 300),
                    _text(raw, "aila", 20), list(requires), servers, settings)


def read_manifest(folder: Path) -> Manifest:
    path = folder / MANIFEST
    if not path.is_file():
        raise ExtensionError(f"这不是艾拉的扩展包：里面没有 {MANIFEST}。")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ExtensionError(f"{MANIFEST} 读不了：{err}") from err
    return parse_manifest(raw)


# ---------- 包里的命令、本事 ----------


def _front(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    m = FRONT.match(text)
    if not m:
        return meta, text
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[m.end():]


def scan_skills(folder: Path, ext_id: str, ext_name: str) -> list[SkillInfo]:
    """skills/<名字>/SKILL.md：名字是文件夹名，简介取开头的 description，没有就取第一行"""
    root = folder / "skills"
    if not root.is_dir():
        return []
    out = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir())[:MAX_SKILLS]:
        doc = sub / "SKILL.md"
        if not SKILL_NAME.fullmatch(sub.name) or not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            continue
        meta, body = _front(text)
        desc = meta.get("description") or next((ln.strip().lstrip("#").strip() for ln in body.splitlines() if ln.strip()), "")
        out.append(SkillInfo(sub.name, desc[:200], sub, ext_id, ext_name))
    return out


def scan_commands(folder: Path) -> list[str]:
    from .runtime.commands import parse_file

    root = folder / "commands"
    if not root.is_dir():
        return []
    return [c.name for p in sorted(root.glob("*.md")) if p.is_file() and (c := parse_file(p, "extension"))]


def read_skill_file(skill: SkillInfo, file: str = "") -> str:
    """读一项本事的全文，或者它文件夹里的另一个文件（只能在这个文件夹里）"""
    rel = (file or "SKILL.md").replace("\\", "/")
    parts = PurePosixPath(rel).parts
    if not parts or rel.startswith("/") or ".." in parts or ":" in rel:
        raise ExtensionError("只能读这项本事自己文件夹里的文件。")
    path = (skill.folder / Path(*parts)).resolve()
    if not path.is_relative_to(skill.folder.resolve()) or not path.is_file():
        raise ExtensionError(f"{skill.name} 里没有 {rel}。")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        raise ExtensionError(f"读不了 {rel}：{err}") from err
    if len(text) > MAX_SKILL_CHARS:
        text = text[:MAX_SKILL_CHARS] + f"\n……（太长，只给了前 {MAX_SKILL_CHARS} 个字）"
    return text


# ---------- 导入：解压、检查 ----------


def _safe_member(name: str) -> PurePosixPath | None:
    """zip 里的路径：不许绝对路径、盘符、..；目录项返回 None"""
    name = name.replace("\\", "/")
    if name.endswith("/"):
        return None
    p = PurePosixPath(name)
    if name.startswith("/") or ":" in name or ".." in p.parts or not p.parts:
        raise ExtensionError(f"包里有不安全的路径：{name}")
    return p


def unpack(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise ExtensionError(f"包里文件太多了（最多 {MAX_FILES} 个）。")
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ExtensionError(f"包解开以后太大了（最多 {MAX_TOTAL // 1024 // 1024}MB）。")
        for info in infos:
            rel = _safe_member(info.filename)
            if rel is None:
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ExtensionError(f"包里有链接：{info.filename}")
            if info.file_size > MAX_FILE:
                raise ExtensionError(f"包里的 {info.filename} 太大了。")
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    # zip 里多包了一层文件夹（右键「压缩」常这样）：说明书在唯一的子文件夹里就往上提一层
    if not (dest / MANIFEST).exists():
        subs = [p for p in dest.iterdir()]
        if len(subs) == 1 and subs[0].is_dir() and (subs[0] / MANIFEST).exists():
            inner = subs[0]
            for child in list(inner.iterdir()):
                child.rename(dest / child.name)
            inner.rmdir()


def _slug(label: str) -> str:
    """文件夹名 → 扩展 id：英文的照着转小写；中文这类转不出来的用名字算一个短号（同一个包再导入是同一个 id）"""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24].strip("-")
    if ID.fullmatch(slug):
        return slug
    return "skill-" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]


def _skill_name(folder: Path, fallback: str) -> str:
    try:
        meta, _ = _front(folder.joinpath("SKILL.md").read_text(encoding="utf-8-sig").replace("\r\n", "\n"))
    except (OSError, UnicodeDecodeError):
        meta = {}
    for name in (meta.get("name", ""), fallback):
        name = re.sub(r"[^\w一-鿿-]+", "-", name).strip("-")[:40]
        if SKILL_NAME.fullmatch(name):
            return name
    return "skill"


def wrap_skills(dest: Path, label: str) -> bool:
    """没有说明书、只有 SKILL.md 的文件夹（别处常见的技能包写法）：自动包成一个只带本事的扩展。

    认三种：SKILL.md 就在最外面；外面多包了一层文件夹；一个文件夹里放了好几项（每个子文件夹一个 SKILL.md）。
    别的文件原样跟着本事走，本事外面的文件丢掉。包好了返回 True，认不出来返回 False（照常报「没有说明书」）
    """
    if (dest / MANIFEST).exists():
        return False
    root = dest
    subs = list(dest.iterdir())
    if not (dest / "SKILL.md").is_file() and len(subs) == 1 and subs[0].is_dir():
        root, label = subs[0], subs[0].name
    if (root / "SKILL.md").is_file():
        found = [(root, label)]
    else:
        found = [(p, p.name) for p in sorted(root.iterdir()) if p.is_dir() and (p / "SKILL.md").is_file()][:MAX_SKILLS]
    if not found:
        return False

    # 先读好名字和简介，再挪文件
    names: list[str] = []
    for folder, fallback in found:
        name = base = _skill_name(folder, fallback)
        n = 2
        while name in names:
            name = f"{base[:37]}-{n}"
            n += 1
        names.append(name)
    first = scan_desc(found[0][0])

    raw = dest.with_name(dest.name + "-raw")
    dest.rename(raw)
    dest.mkdir()
    skills = dest / "skills"
    skills.mkdir()
    for (folder, _), name in zip(found, names):
        (raw / folder.relative_to(dest)).rename(skills / name)
    shutil.rmtree(raw, ignore_errors=True)
    manifest = {
        "id": _slug(label),
        "name": label[:40] or "技能包",
        "version": "1.0.0",
        "description": (first or "从 SKILL.md 导入的技能包")[:300],
    }
    (dest / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def scan_desc(folder: Path) -> str:
    """一项本事的一句话简介：开头的 description，没有就取第一行"""
    try:
        text = folder.joinpath("SKILL.md").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return ""
    meta, body = _front(text)
    return (meta.get("description") or next((ln.strip().lstrip("#").strip() for ln in body.splitlines() if ln.strip()), ""))[:200]


def copy_folder(src: Path, dest: Path) -> None:
    count = total = 0
    for path in src.rglob("*"):
        if path.is_symlink():
            raise ExtensionError(f"文件夹里有链接：{path.relative_to(src)}")
        if path.is_file():
            count += 1
            total += path.stat().st_size
    if count > MAX_FILES or total > MAX_TOTAL:
        raise ExtensionError("扩展文件夹太大了。")
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules/.cache"))


# ---------- 装好的扩展 ----------


@dataclass
class Extension:
    manifest: Manifest
    folder: Path
    enabled: bool
    # 服务名 → 停没停（装好时都停着）
    server_disabled: dict[str, bool] = field(default_factory=dict)
    always_allow: dict[str, list[str]] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.manifest.id

    def server_name(self, server: str) -> str:
        """MCP 那边看到的服务名（工具叫 mcp__扩展id-服务名__工具）"""
        return f"{self.id}-{server}"

    def missing_settings(self) -> list[str]:
        return [s.label for s in self.manifest.settings if s.required and not self.settings.get(s.key)]


def plan(folder: Path, installed: "Extension | None" = None) -> dict[str, Any]:
    """装之前给你看的清单：全部从说明书和文件算出来，不用扩展自己的介绍"""
    m = read_manifest(folder)
    warnings: list[str] = []
    if not version_ok(m.aila):
        warnings.append(f"这个扩展要艾拉 {m.aila}，现在是 {AILA_VERSION}，装了可能用不了。")
    missing = [r for r in m.requires if shutil.which(r) is None]
    if missing:
        warnings.append(f"这台电脑上没找到 {('、'.join(missing))}：它的工具服务要用到，先装好再打开。")
    skills = scan_skills(folder, m.id, m.name)
    if skills:
        warnings.append("它会给她新的做事说明（下面的「本事」）：用得上的时候她会照着做。只装你信得过的扩展。")
    if m.servers:
        warnings.append("它会在你的电脑上运行下面的程序（装好后先停着，你打开才运行）。只读之外的工具每次用都会先问你。")
    try:
        extras = extras_summary(folder, strict=True)  # 动作、台词写错了导入时就说，不等装好以后默默不生效
    except ExtrasError as err:
        raise ExtensionError(str(err)) from err
    if extras["linesReplace"] and extras["lines"]:
        warnings.append("它会整组换掉她在这些场合说的话：" + "、".join(extras["lines"]) + "。")
    if extras["pages"]:
        warnings.append("它带了页面：在单独的沙盒窗口里打开，不能联网，也碰不到艾拉的服务。")
    if installed is not None:
        warnings.append(f"已经装了 {installed.manifest.version} 版，会换成 {m.version} 版（开关、设置、「以后都不用问」保留）。")

    def show(text: str) -> str:
        return text.replace("${extension}", "<扩展文件夹>")

    return {
        "id": m.id,
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "commands": scan_commands(folder),
        "skills": [{"name": s.name, "description": s.description} for s in skills],
        "servers": [{"name": n, "command": " ".join(show(x) for x in [c["command"], *c["args"]]),
                     "readOnly": c["readOnly"], "env": sorted(c["env"])} for n, c in m.servers.items()],
        "settings": [{"key": s.key, "label": s.label, "secret": s.secret, "required": s.required,
                      "description": s.description} for s in m.settings],
        "requires": m.requires,
        **extras,
        "warnings": warnings,
        "upgrade": installed is not None,
    }


class ExtensionStore:
    """<数据目录>/extensions/：每个扩展一个文件夹，开关和设置在 state.json"""

    def __init__(self, root: Path) -> None:
        self.root = root

    # ----- 状态 -----

    def _state(self) -> dict[str, Any]:
        try:
            data = json.loads((self.root / STATE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (STATE + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.root / STATE)

    def _entry(self, data: dict[str, Any], ext_id: str) -> dict[str, Any]:
        entry = data.setdefault(ext_id, {})
        entry.setdefault("enabled", True)
        entry.setdefault("servers", {})
        entry.setdefault("settings", {})
        return entry

    # ----- 列出 -----

    def installed(self) -> list[Extension]:
        if not self.root.is_dir():
            return []
        data = self._state()
        out = []
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                m = read_manifest(folder)
            except ExtensionError:
                continue
            if m.id != folder.name:
                continue
            entry = data.get(m.id, {})
            servers = entry.get("servers", {}) if isinstance(entry.get("servers"), dict) else {}
            out.append(Extension(
                m, folder, entry.get("enabled", True) is not False,
                {n: (servers.get(n, {}) or {}).get("disabled", True) is not False for n in m.servers},
                {n: [t for t in (servers.get(n, {}) or {}).get("alwaysAllow", []) if isinstance(t, str)] for n in m.servers},
                {k: v for k, v in (entry.get("settings") or {}).items() if isinstance(v, str)},
            ))
        return out

    def get(self, ext_id: str) -> Extension:
        for ext in self.installed():
            if ext.id == ext_id:
                return ext
        raise ExtensionError(f"没有装 {ext_id} 这个扩展。")

    # ----- 导入 -----

    def stage(self, source: Path) -> tuple[str, dict[str, Any]]:
        """把 zip 或文件夹先放进暂存区检查，返回（暂存号，清单）。真装要再调 install(暂存号)"""
        source = source.expanduser()
        if not source.exists():
            raise ExtensionError(f"找不到 {source}。")
        staging = self.root / STAGING
        token = secrets.token_hex(8)
        dest = staging / token
        dest.mkdir(parents=True)
        try:
            if source.is_dir():
                copy_folder(source, dest)
            elif zipfile.is_zipfile(source):
                unpack(source, dest)
            else:
                raise ExtensionError("扩展包要是一个文件夹或者 .zip。")
            wrap_skills(dest, source.stem if source.is_file() else source.name)
            m = read_manifest(dest)
            installed = next((e for e in self.installed() if e.id == m.id), None)
            return token, plan(dest, installed)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    def install(self, token: str) -> Extension:
        if not re.fullmatch(r"[0-9a-f]{16}", token):
            raise ExtensionError("暂存号不对。")
        src = self.root / STAGING / token
        if not src.is_dir():
            raise ExtensionError("没找到要装的扩展（可能已经装过或者过期了），重新导入一次。")
        m = read_manifest(src)
        target = self.root / m.id
        if target.exists():
            shutil.rmtree(target)
        src.rename(target)
        data = self._state()
        entry = self._entry(data, m.id)
        # 新的服务默认停着；说明书里没了的服务、设置清掉
        entry["servers"] = {n: entry["servers"].get(n, {"disabled": True}) for n in m.servers}
        keys = {s.key for s in m.settings}
        entry["settings"] = {k: v for k, v in entry["settings"].items() if k in keys}
        self._save(data)
        return self.get(m.id)

    def discard(self, token: str) -> None:
        if re.fullmatch(r"[0-9a-f]{16}", token):
            shutil.rmtree(self.root / STAGING / token, ignore_errors=True)

    def uninstall(self, ext_id: str) -> None:
        ext = self.get(ext_id)
        shutil.rmtree(ext.folder)
        data = self._state()
        data.pop(ext_id, None)
        self._save(data)

    # ----- 开关、设置 -----

    def set_enabled(self, ext_id: str, enabled: bool) -> None:
        self.get(ext_id)
        data = self._state()
        self._entry(data, ext_id)["enabled"] = enabled
        self._save(data)

    def set_server_disabled(self, ext_id: str, server: str, disabled: bool) -> None:
        ext = self.get(ext_id)
        if server not in ext.manifest.servers:
            raise ExtensionError(f"{ext.manifest.name} 没有叫 {server} 的工具服务。")
        if not disabled and ext.missing_settings():
            raise ExtensionError(f"先在设置里填好：{'、'.join(ext.missing_settings())}。")
        data = self._state()
        self._entry(data, ext_id)["servers"].setdefault(server, {})["disabled"] = disabled
        self._save(data)

    def set_settings(self, ext_id: str, values: dict[str, str]) -> None:
        """空字符串表示清掉；没声明的键不收"""
        ext = self.get(ext_id)
        keys = {s.key for s in ext.manifest.settings}
        data = self._state()
        stored = self._entry(data, ext_id)["settings"]
        for key, value in values.items():
            if key not in keys:
                raise ExtensionError(f"{ext.manifest.name} 没有 {key} 这项设置。")
            if not isinstance(value, str) or len(value) > MAX_SETTING_CHARS:
                raise ExtensionError(f"{key} 要是文字，最多 {MAX_SETTING_CHARS} 个字。")
            if value.strip():
                stored[key] = value.strip()
            else:
                stored.pop(key, None)
        self._save(data)

    def allow(self, ext_id: str, server: str, tool: str) -> None:
        data = self._state()
        entry = self._entry(data, ext_id)["servers"].setdefault(server, {"disabled": False})
        allow = entry.setdefault("alwaysAllow", [])
        if tool not in allow:
            allow.append(tool)
            self._save(data)

    def forget(self, ext_id: str, server: str, tool: str) -> bool:
        data = self._state()
        entry = self._entry(data, ext_id)["servers"].get(server, {})
        allow = entry.get("alwaysAllow", [])
        if tool not in allow:
            return False
        allow.remove(tool)
        self._save(data)
        return True

    # ----- 给别的地方用 -----

    def enabled(self) -> list[Extension]:
        return [e for e in self.installed() if e.enabled]

    def command_dirs(self) -> list[tuple[str, Path]]:
        return [(e.id, e.folder / "commands") for e in self.enabled() if (e.folder / "commands").is_dir()]

    def skills(self) -> list[SkillInfo]:
        out: list[SkillInfo] = []
        seen: set[str] = set()
        for e in self.enabled():
            for s in scan_skills(e.folder, e.id, e.manifest.name):
                if s.name.lower() not in seen:  # 同名的先装（按 id 排序）的那个算
                    seen.add(s.name.lower())
                    out.append(s)
        return out

    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        """启用的扩展里的工具服务，按 mcp.json 的格式（服务名「扩展id-服务名」），设置填进去。
        停着的服务也给（带 disabled），设置页才看得到；扩展停用了就整个不给"""
        out: dict[str, dict[str, Any]] = {}
        for e in self.enabled():
            for name, cfg in e.manifest.servers.items():
                def fill(text: str) -> str:
                    text = text.replace("${extension}", str(e.folder))
                    return PLACEHOLDER.sub(lambda m: e.settings.get(m.group(1), ""), text)

                out[e.server_name(name)] = {
                    "command": fill(cfg["command"]),
                    "args": [fill(a) for a in cfg["args"]],
                    "env": {k: fill(v) for k, v in cfg["env"].items()},
                    "cwd": str(e.folder),
                    "readOnly": cfg["readOnly"],
                    "disabled": e.server_disabled.get(name, True),
                    "alwaysAllow": list(e.always_allow.get(name, [])),
                    "name": f"{e.manifest.name} · {name}",
                    "extension": e.id,
                }
        return out

    def owner(self, server_name: str) -> tuple[str, str] | None:
        """「扩展id-服务名」→（扩展 id，服务名）；不是扩展的服务返回 None"""
        for e in self.installed():
            for name in e.manifest.servers:
                if e.server_name(name) == server_name:
                    return e.id, name
        return None

    def describe(self, ext: Extension) -> dict[str, Any]:
        """给界面看的：密钥类设置只说填没填"""
        return {
            "id": ext.id,
            "name": ext.manifest.name,
            "version": ext.manifest.version,
            "description": ext.manifest.description,
            "enabled": ext.enabled,
            "folder": str(ext.folder),
            "commands": scan_commands(ext.folder),
            "skills": [{"name": s.name, "description": s.description} for s in scan_skills(ext.folder, ext.id, ext.manifest.name)],
            "servers": [{"name": n, "serverName": ext.server_name(n), "disabled": ext.server_disabled.get(n, True),
                         "readOnly": c["readOnly"]} for n, c in ext.manifest.servers.items()],
            "settings": [{"key": s.key, "label": s.label, "secret": s.secret, "required": s.required,
                          "description": s.description, "set": bool(ext.settings.get(s.key)),
                          "value": "" if s.secret else ext.settings.get(s.key, "")} for s in ext.manifest.settings],
            "missing": ext.missing_settings(),
            "requires": ext.manifest.requires,
            **extras_summary(ext.folder),
        }

    # ----- 第二期：桌面伙伴（动作、台词、形象） -----

    def motions(self) -> list[Motion]:
        """启用的扩展带的动作；重名的先装（按 id 排序）的那个算"""
        out: list[Motion] = []
        seen: set[str] = set()
        for e in self.enabled():
            for m in scan_motions(e.folder):
                if m.name not in seen:
                    seen.add(m.name)
                    out.append(m)
        return out

    def companion(self) -> dict[str, Any]:
        """给桌面伙伴窗口的：动作（姿态段）、台词（加进去的、整组换掉的）、能换的形象"""
        add: dict[str, list[str]] = {}
        replace: dict[str, list[str]] = {}
        avatars = []
        for e in self.enabled():
            lines, whole = read_lines(e.folder)
            for key, items in lines.items():
                target = replace if whole else add
                target.setdefault(key, []).extend(items)
            avatars += [{"extension": e.id, "extensionName": e.manifest.name, **a} for a in scan_avatars(e.folder)]
        return {
            "motions": {m.name: {"label": m.label, "phases": m.phases} for m in self.motions()},
            "lines": {"add": add, "replace": replace},
            "avatars": avatars,
        }

    def avatar_file(self, ext_id: str, name: str) -> Path:
        ext = self.get(ext_id)
        if not ext.enabled:
            raise ExtensionError(f"「{ext.manifest.name}」停用了。")
        found = next((a for a in scan_avatars(ext.folder) if a["name"] == name), None)
        if found is None:
            raise ExtensionError(f"「{ext.manifest.name}」里没有叫 {name} 的形象。")
        return Path(found["file"])

    # ----- 第三期：扩展自带的页面 -----

    def page(self, ext_id: str, name: str) -> Path:
        ext = self.get(ext_id)
        if not ext.enabled:
            raise ExtensionError(f"「{ext.manifest.name}」停用了。")
        try:
            return page_file(ext.folder, name)
        except ExtrasError as err:
            raise ExtensionError(str(err)) from err


def skills_context(skills: list[SkillInfo]) -> str:
    """会话开头的环境说明里加的一段：只有名字和一句话简介，全文用 read_skill 按需读"""
    if not skills:
        return ""
    lines = [f"- {s.name}（{skill_source(s)}）：{s.description}" for s in skills]
    return ("\n\n<可用的本事>\n手上的事用得上哪一项，先用 read_skill 读它的全文，再照着做：\n"
            + "\n".join(lines) + "\n</可用的本事>")


def skill_source(skill: SkillInfo) -> str:
    return f"来自扩展「{skill.extension_name}」" if skill.extension else "艾拉内置"
