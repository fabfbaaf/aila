"""手机连艾拉（局域网）：同一个 Wi-Fi 下，手机浏览器打开电脑的地址，和她聊天、看任务、开任务。

为什么单独开一个门（gateway.py），不把 AI 服务直接放到局域网上：
AI 服务的接口能改配置、加 MCP 服务（等于在这台电脑上跑程序）、同意她改文件跑命令，只能留在本机（security.py）。
手机这个门只放几件事：聊天、看任务、开任务、接着说、停下。同意她改文件、跑命令只能在电脑上点。

怎么配对：
- 默认关着。设置 → 手机 打开以后才在局域网上开端口（AILA_PHONE_PORT，默认 8780）
- 设置页给一个 6 位配对码（5 分钟内有效，输错 5 次作废），手机上输进去，换回这台手机自己的设备密钥
- 密钥只存哈希（data/phone.json），设置页能看到配过哪些手机、随时删掉；关掉开关端口立刻关
- 局域网里是 http：同一个 Wi-Fi 里的人理论上能看到内容。家里的网络用，公共 Wi-Fi 别开（设置页写着）
"""

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("aila.phone")

PAIR_SECONDS = 300
PAIR_TRIES = 5
MAX_DEVICES = 10
SEEN_EVERY = 60.0  # 「最近一次连」多久写一次盘


@dataclass
class Device:
    id: str
    name: str
    token_hash: str
    created: float
    last_seen: float = 0.0

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "created": self.created, "lastSeen": self.last_seen}


@dataclass
class Pairing:
    code: str
    expires: float
    tries: int = 0


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class PhoneStore:
    """开关、配过的手机（data/phone.json）和当前的配对码（只在内存里）"""

    path: Path
    enabled: bool = False
    devices: list[Device] = field(default_factory=list)
    pairing: Pairing | None = None
    # 桌面端的技能包开关：手机上聊天也按它来（桌面端每次聊天都带着，这里记下最新的一份）
    skills: dict[str, bool] = field(default_factory=dict)
    _saved_seen: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @staticmethod
    def load(path: Path) -> "PhoneStore":
        store = PhoneStore(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            store.enabled = bool(data.get("enabled"))
            store.skills = {str(k): bool(v) for k, v in (data.get("skills") or {}).items()}
            store.devices = [Device(**{k: d[k] for k in ("id", "name", "token_hash", "created", "last_seen") if k in d})
                             for d in data.get("devices", []) if isinstance(d, dict)]
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, KeyError) as err:
            log.warning("手机配置读不了，当成没配过：%s", err)
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"enabled": self.enabled, "skills": self.skills, "devices": [asdict(d) for d in self.devices]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def remember_skills(self, skills: dict[str, bool]) -> None:
        """记下桌面端最新的技能包开关（变了才写盘）"""
        clean = {str(k)[:100]: bool(v) for k, v in list(skills.items())[:200]}
        if clean != self.skills:
            self.skills = clean
            self.save()

    # ---------- 配对 ----------

    def new_pairing(self, now: float | None = None) -> Pairing:
        """换一个新的配对码（旧的作废）"""
        self.pairing = Pairing(f"{secrets.randbelow(1_000_000):06d}", (now or time.time()) + PAIR_SECONDS)
        return self.pairing

    def pair(self, code: str, name: str, now: float | None = None) -> str | None:
        """配对码对了就发一个设备密钥（只在这里出现一次），不对返回 None"""
        now = now or time.time()
        with self._lock:
            p = self.pairing
            if p is None or now > p.expires:
                self.pairing = None
                return None
            if not hmac.compare_digest(p.code, code.strip()):
                p.tries += 1
                if p.tries >= PAIR_TRIES:
                    self.pairing = None  # 输错太多次：作废，要在电脑上重新拿
                return None
            self.pairing = None  # 一个码只能配一台
            token = secrets.token_urlsafe(32)
            name = (name.strip() or "手机")[:40]
            self.devices = [*self.devices, Device(secrets.token_hex(4), name, _hash(token), now, now)][-MAX_DEVICES:]
            self.save()
            return token

    def device(self, token: str) -> Device | None:
        """这个密钥是哪台手机的（顺手记下最近一次连）"""
        if not token:
            return None
        digest = _hash(token)
        found = next((d for d in self.devices if hmac.compare_digest(d.token_hash, digest)), None)
        if found is not None:
            now = time.time()
            found.last_seen = now
            if now - self._saved_seen > SEEN_EVERY:
                self._saved_seen = now
                self.save()
        return found

    def forget(self, device_id: str) -> bool:
        before = len(self.devices)
        self.devices = [d for d in self.devices if d.id != device_id]
        if len(self.devices) != before:
            self.save()
            return True
        return False


def lan_addresses() -> list[str]:
    """这台电脑在局域网里的 IPv4 地址（192.168.x.x、10.x.x.x 这类），手机用它连"""
    found: set[str] = set()
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(str(info[4][0]))
    # 有的机器 hostname 只解析出 127.0.0.1：问一下系统往外连会用哪个地址（UDP 不真的发包）
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.168.0.1", 9))
        found.add(str(s.getsockname()[0]))
    out = []
    for a in found:
        with contextlib.suppress(ValueError):
            ip = ipaddress.ip_address(a)
            if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                out.append(a)
    # 家里路由器常见的 192.168 排前面；172.x 常是 WSL、Docker 的虚拟网卡，手机连不上，排最后
    rank = {"192": 0, "10": 1, "172": 2}
    return sorted(out, key=lambda a: (rank.get(a.split(".")[0], 3), a))


class PhoneServer:
    """局域网上的那个门：在 AI 服务的进程里再跑一个 uvicorn，开关随时生效"""

    def __init__(self, port: int, host: str = "0.0.0.0") -> None:  # noqa: S104  局域网里的手机要连得上
        self.port = port
        self.host = host
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None
        self.error = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, app: Any) -> None:
        if self.running:
            return
        import uvicorn

        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]  # Ctrl+C 交给主服务处理
        self._server = server
        self.error = ""
        self._task = asyncio.create_task(server.serve())
        for _ in range(50):  # 等它真的开始监听（端口被占用时 serve 会直接结束）
            await asyncio.sleep(0.05)
            if server.started or self._task.done():
                break
        if self._task.done() or not server.started:
            self.error = f"端口 {self.port} 开不了（可能被别的程序占着）。"
            log.warning("手机连接：%s", self.error)
            self._task = None

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, 5)
        self._task = None
        self._server = None
