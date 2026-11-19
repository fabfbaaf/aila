"""扩展目录：从一份 catalog.json 浏览扩展，点一下下载、检查、给你看清单，你点「装」才装（第三期）。

目录长这样：
    {
      "name": "艾拉扩展目录",
      "extensions": [
        {"id": "dice", "name": "骰子", "version": "1.0.0", "description": "……",
         "url": "dice-1.0.0.zip", "sha256": "64 位十六进制", "size": 12345}
      ]
    }

目录地址可以是 https 网址，也可以是这台电脑上的 catalog.json（团队共享盘、自己测试用）。
url 相对目录地址算；本机目录里的 zip 只能在目录文件所在的文件夹里。

为什么这样设计：
- 下载的地址来自目录的内容（不是你亲手输入的），所以只许 https、只许公网地址，每一跳重定向都重新检查
  （和读网页同一套检查），本机和内网的地址一律拦下，免得一份目录把请求引到你电脑上的服务
- 下载下来的 zip 要和目录里写的 sha256 一致，大小有上限；zip 里说明书的 id 也要和目录里写的一样
- 下完以后走和「导入」完全一样的路：放进暂存区检查、给你看代码算出来的清单，你点了才装
- 现在没有官方目录：默认一个目录都没有，你自己加
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .extensions import ID, VERSION, ExtensionError, ExtensionStore
from .tools.reader import PageError, check_public_url

CATALOGS_FILE = "catalogs.json"
MAX_CATALOGS = 10
MAX_CATALOG_BYTES = 1024 * 1024
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 500
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(30.0, read=120.0)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    version: str
    description: str
    url: str
    sha256: str
    size: int | None


def _is_remote(source: str) -> bool:
    return source.lower().startswith(("https://", "http://"))


def _local_path(source: str) -> Path:
    if source.lower().startswith("file:///"):
        source = source[8:]
    return Path(source).expanduser()


def check_source(source: str) -> str:
    """目录地址：https 网址或者本机的文件；别的都不收"""
    source = source.strip()
    if not source or len(source) > 1000:
        raise ExtensionError("目录地址不能是空的，也不能太长。")
    if _is_remote(source):
        if not source.lower().startswith("https://"):
            raise ExtensionError("网上的目录只认 https 地址。")
        return source
    path = _local_path(source)
    if not path.is_absolute():
        raise ExtensionError("本机的目录要写完整路径（比如 D:\\exts\\catalog.json）。")
    return str(path)


def load_sources(store: ExtensionStore) -> list[str]:
    try:
        data = json.loads((store.root / CATALOGS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    urls = data.get("urls", []) if isinstance(data, dict) else []
    return [u for u in urls if isinstance(u, str)][:MAX_CATALOGS]


def save_sources(store: ExtensionStore, urls: list[str]) -> list[str]:
    clean: list[str] = []
    for u in urls:
        s = check_source(u)
        if s not in clean:
            clean.append(s)
    if len(clean) > MAX_CATALOGS:
        raise ExtensionError(f"最多 {MAX_CATALOGS} 个目录。")
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / CATALOGS_FILE).write_text(json.dumps({"urls": clean}, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


async def _get(url: str, client: httpx.AsyncClient, limit: int, resolve: Any = None) -> bytes:
    """GET 公网 https 地址：每一跳都检查，超过 limit 就停"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not current.lower().startswith("https://"):
            raise ExtensionError("目录和扩展包只能从 https 地址下载。")
        try:
            await (check_public_url(current, resolve) if resolve else check_public_url(current))
        except PageError as err:
            raise ExtensionError(f"{urlsplit(current).hostname}：{err}") from err
        async with client.stream("GET", current, follow_redirects=False, timeout=TIMEOUT) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise ExtensionError("下载地址跳转了，但没说跳到哪。")
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise ExtensionError(f"下载失败（HTTP {resp.status_code}）：{current}")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise ExtensionError(f"太大了（超过 {limit // 1024 // 1024 or 1}MB），不下了。")
            return bytes(body)
    raise ExtensionError("跳转次数太多。")


def parse_catalog(raw: Any) -> tuple[str, list[Entry]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("extensions"), list):
        raise ExtensionError("目录格式不对：要有 extensions 列表。")
    entries: list[Entry] = []
    for item in raw["extensions"][:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        ext_id, version, url, sha = (str(item.get(k, "")) for k in ("id", "version", "url", "sha256"))
        if not ID.fullmatch(ext_id) or not VERSION.fullmatch(version) or not url or not SHA256.fullmatch(sha.lower()):
            continue  # 写错的条目跳过，不影响别的
        size = item.get("size")
        entries.append(Entry(ext_id, str(item.get("name") or ext_id)[:40], version,
                             str(item.get("description") or "")[:300], url[:1000], sha.lower(),
                             size if isinstance(size, int) and size > 0 else None))
    name = raw.get("name")
    return (name[:40] if isinstance(name, str) and name.strip() else ""), entries


async def read_catalog(source: str, client: httpx.AsyncClient, resolve: Any = None) -> tuple[str, list[Entry]]:
    if _is_remote(source):
        data = await _get(source, client, MAX_CATALOG_BYTES, resolve)
    else:
        path = _local_path(source)
        if not path.is_file():
            raise ExtensionError(f"找不到目录文件 {path}。")
        if path.stat().st_size > MAX_CATALOG_BYTES:
            raise ExtensionError("目录文件太大了。")
        data = path.read_bytes()
    try:
        return parse_catalog(json.loads(data.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ExtensionError(f"目录不是合法的 JSON：{err}") from err


async def browse(store: ExtensionStore, client: httpx.AsyncClient, resolve: Any = None) -> list[dict[str, Any]]:
    """所有目录里的扩展，标上装没装、能不能升级；一个目录读不了只在它那里写原因"""
    installed = {e.id: e.manifest.version for e in store.installed()}
    out = []
    for source in load_sources(store):
        try:
            name, entries = await read_catalog(source, client, resolve)
            out.append({"source": source, "name": name or source, "error": "", "extensions": [
                {"id": e.id, "name": e.name, "version": e.version, "description": e.description, "size": e.size,
                 "installed": installed.get(e.id),
                 "upgrade": e.id in installed and _newer(e.version, installed[e.id])} for e in entries]})
        except (ExtensionError, httpx.HTTPError, OSError) as err:
            out.append({"source": source, "name": source, "error": str(err), "extensions": []})
    return out


def _newer(a: str, b: str) -> bool:
    def t(v: str) -> tuple[int, ...]:
        parts = [int(p) for p in v.split(".")]
        return tuple(parts + [0] * (3 - len(parts)))
    return t(a) > t(b)


async def fetch_and_stage(store: ExtensionStore, source: str, ext_id: str, client: httpx.AsyncClient,
                          resolve: Any = None) -> tuple[str, dict[str, Any]]:
    """从目录下载一个扩展：校验大小和 sha256，放进暂存区检查，返回（暂存号，清单）"""
    if source not in load_sources(store):
        raise ExtensionError("没有这个目录（先在目录地址里加上）。")
    _, entries = await read_catalog(source, client, resolve)
    entry = next((e for e in entries if e.id == ext_id), None)
    if entry is None:
        raise ExtensionError(f"目录里没有 {ext_id}。")
    if _is_remote(source):
        data = await _get(urljoin(source, entry.url), client, MAX_ZIP_BYTES, resolve)
    else:
        base = _local_path(source).parent.resolve()
        path = (base / entry.url).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ExtensionError(f"目录里 {ext_id} 的文件不在目录所在的文件夹里，或者找不到。")
        if path.stat().st_size > MAX_ZIP_BYTES:
            raise ExtensionError("扩展包太大了。")
        data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry.sha256:
        raise ExtensionError(f"下载的文件和目录里写的对不上（sha256 不一致），没装。可能被改过，或者目录没更新。")
    store.root.mkdir(parents=True, exist_ok=True)
    tmp = store.root / f".download-{ext_id}.zip"
    tmp.write_bytes(data)
    try:
        token, plan = store.stage(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if plan["id"] != ext_id:
        store.discard(token)
        raise ExtensionError(f"包里的扩展是 {plan['id']}，不是目录里写的 {ext_id}，没装。")
    plan["source"] = source
    return token, plan
