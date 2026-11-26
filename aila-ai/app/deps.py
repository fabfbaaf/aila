"""各接口共用的东西：配置、大模型、各种本地模型（后台加载）、技能包、外部 MCP 服务。

本地模型都是「第一次用到时加载，加载失败就关掉那项功能」：缺了语音模型照样能打字聊天，
缺了向量模型记忆就退化成「带上最近几条」，知识库只用关键词检索。
"""

import asyncio
import logging
import os
import threading
import time
from dataclasses import replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .notify import Notifier
    from .runtime.schedules import Scheduler, ScheduleStore
    from .agenda import Calendar
    from .tools.agenda import MeetingWatcher
    from .phone import PhoneServer, PhoneStore
    from .android import Adb, AndroidSession, AndroidSettings

from . import envfile
from .config import ENV_FILE as CONFIG_ENV_FILE
from .config import load_settings
from .confirm import Confirmations
from .knowledge.rerank import Score, local_reranker
from .knowledge import KnowledgeBase, knowledge_skill
from .llm import ChatModel, CoderModel, MockModel, OpenAICompatibleModel
from .memory.episodes import Digester
from .memory.store import MemoryStore
from .browser import BrowserSession
from .runtime.loop import AgentConfig
from .runtime.mock import MockCoderModel
from .runtime.runner import Runtime
from .code import CodeProjects
from .diary import Diary
from .review import Reviews
from .extensions import ExtensionStore
from .persona import persona_addendum
from .studio import Studio
from .tools import Skill, SkillRegistry
from .tools.browser import browse_skill, browser_skill
from .tools.code import code_skill
from .tools.coding import coding_skill
from .tools.computer import computer_skill, computer_task_skill
from .tools.reminders import reminder_skill
from .tools.mcp import McpHub, load_config
from .tools.reader import make_resolver
from .tools.search import BochaSearch, FreeSearch, MockSearch, SearchProvider, TavilySearch
from .tools.agenda import calendar_skill, mail_skill
from .tools.android import android_skill
from .tools.guide import guide_skill
from .tools.schedule import schedule_skill
from .tools.web import web_skill
from .tools.x_browser import x_browser_skill

log = logging.getLogger("aila")

settings = load_settings()
# 设置界面改配置时写这个文件（测试里换成临时文件）
ENV_FILE = CONFIG_ENV_FILE
# 搜索和读网页共用一个连接池
http_client = httpx.AsyncClient()

# /health 用：off / loading / ready / 出错原因
# idle：开着但还没用到（语音模型用到时才加载）；off：关掉了（AILA_VOICE=off）
status: dict[str, str] = {"ears": "idle" if settings.voice else "off", "tts": "idle" if settings.voice else "off", "embed": "off"}
# 正在等用户回答的工具确认（所有对话共用）
confirmations = Confirmations()


def get_confirmations() -> Confirmations:
    return confirmations


def get_extensions() -> ExtensionStore:
    """装好的扩展包（<数据目录>/extensions）。每次现取：测试会换数据目录"""
    return ExtensionStore(settings.data_dir / "extensions")


@lru_cache
def get_model() -> ChatModel:
    if settings.mock:
        log.warning("没有配置 LLM_API_KEY，使用假模型（只会几句固定回复）")
        return MockModel()
    log.info("大模型：%s（%s）", settings.llm_model, settings.llm_base_url)
    return OpenAICompatibleModel(settings)


# ---------- 编程 Agent ----------


def get_coder_model() -> CoderModel:
    if settings.mock:
        return MockCoderModel()
    name = settings.coder_model or settings.llm_model
    return OpenAICompatibleModel(replace(settings, llm_model=name))


def get_coder_fast_model() -> CoderModel:
    """查资料的子 Agent 用的模型。不在一轮里来回换模型（前缀缓存按模型算），所以是按角色分：主 Agent 一个、子 Agent 一个"""
    if settings.mock:
        return MockCoderModel()
    name = settings.coder_fast_model or settings.coder_model or settings.llm_model
    return OpenAICompatibleModel(replace(settings, llm_model=name))


@lru_cache
def get_runtime() -> Runtime:
    config = AgentConfig(max_steps=settings.coder_max_steps, context_window=settings.coder_context)
    # 用函数而不是直接传模型：设置里换了密钥或模型以后，新会话用新的
    runtime = Runtime(settings.data_dir, lambda: get_coder_model(), config=config, code_projects=lambda: get_code_projects(),
                      fast_factory=lambda: get_coder_fast_model(), mcp_config=settings.mcp_config,
                      computer_inventory=settings.computer_inventory, extensions=get_extensions(),
                      memory_store=lambda: get_memory_store(), language=lambda: settings.language,
                      web_search=search_provider, knowledge=get_knowledge, reranker=get_reranker)
    runtime.mcp.on_change = restart_mcp  # 编程这边改了 mcp.json，对话那边跟着重连
    runtime.watchers.append(get_notifier().watch)  # 等你同意、做完了：告诉手机（app/notify.py）
    return runtime


def get_reranker() -> Score | None:
    return local_reranker(settings.rerank_model_dir)


def _load(kind: str, build: Any) -> Any:
    status[kind] = "loading"
    try:
        t0 = time.perf_counter()
        obj = build()
        status[kind] = "ready"
        log.info("模型（%s）加载完成，%.1f 秒", kind, time.perf_counter() - t0)
        return obj
    except Exception as err:  # 缺模型、配置写错：这项功能不可用，但服务照常启动
        status[kind] = str(err)
        log.warning("模型（%s）加载失败：%s", kind, err)
        return None


# ---------- 语音 ----------

_ears_lock, _voice_lock, _embed_lock = threading.Lock(), threading.Lock(), threading.Lock()


@lru_cache
def _ears() -> Any:
    if not settings.voice:
        return None
    from .voice.engines import EarConfig, Ears  # sherpa-onnx 只在用到语音时才导入

    cfg = EarConfig(settings.models_dir, wake_threshold=settings.wake_threshold,
                    end_silence=settings.end_silence, language=settings.asr_language)
    return _load("ears", lambda: Ears(cfg))


@lru_cache
def _voice() -> Any:
    if not settings.voice:
        return None
    from .voice.tts import TtsConfig, Voice

    # 合成越快她开口越早：用一半逻辑核，最多 8 个（实测 4 → 8 线程，短句 330 → 230 毫秒）
    threads = min(8, max(2, (os.cpu_count() or 4) // 2))
    cfg = TtsConfig(settings.models_dir, settings.tts_engine, settings.tts_speaker, settings.tts_speed, threads)
    return _load("tts", lambda: Voice(cfg))


def get_ears() -> Any:
    with _ears_lock:  # 后台预加载和第一个请求可能同时到，只加载一次
        return _ears()


def get_voice() -> Any:
    with _voice_lock:
        return _voice()


# ---------- 向量模型（记忆、知识库共用） ----------


@lru_cache
def _embedder() -> Any:
    from .memory import FastEmbedder

    return _load("embed", lambda: FastEmbedder(settings.models_dir))


def get_embedder() -> Any:
    with _embed_lock:
        return _embedder()


# ---------- 知识库和技能包 ----------


@lru_cache
def get_knowledge() -> KnowledgeBase:
    return KnowledgeBase(settings.data_dir / "knowledge.sqlite3", get_embedder)


@lru_cache
def get_memory_store() -> MemoryStore:
    """记忆库（memory/store.py）：关于你的事、对话摘要、编程任务的经历、项目约定。向量模型用到时现取"""
    return MemoryStore(settings.data_dir / "memory.sqlite3", lambda: get_embedder())


# 滑出对话窗口的那段：后台写摘要（同一时间只做一份）
digester = Digester()


def search_provider() -> SearchProvider | None:
    """联网搜索用哪家：填了密钥用博查或 Tavily；没填用免费搜索（读必应结果页）；SEARCH_PROVIDER=none 是关掉联网搜索"""
    if settings.search_provider == "none":
        return None
    if settings.search_api_key:
        providers = {"bocha": BochaSearch, "tavily": TavilySearch}
        if settings.search_provider not in providers:
            raise ValueError(f"SEARCH_PROVIDER 只能是 {' / '.join(providers)}")
        return providers[settings.search_provider](settings.search_api_key, http_client)
    if settings.mock:
        return MockSearch()  # 假模型配假搜索，整条链路不联网也能调
    return FreeSearch(http_client)


# 已经连上的外部 MCP 服务：重建技能包时（改了搜索设置）要重新加进去
_mcp_skills: list[Skill] = []


@lru_cache
def get_code_projects() -> CodeProjects:
    """用户登记的代码项目，索引在 data/code（代码只在本机建索引）"""
    return CodeProjects(settings.data_dir, lambda: get_embedder())  # 用到时再查（测试里可以换掉）


def _persona() -> str:
    """设置里调的性格（每次现读：改了设置马上生效）"""
    return persona_addendum(settings.persona_style, settings.persona_call, settings.persona_catchphrase, settings.language)


@lru_cache
def get_studio() -> Studio:
    """她的作品：每件一个文件夹（data/works），是一个只在那个文件夹里做事的编程会话"""
    return Studio(settings.data_dir, lambda: get_runtime(), lambda: get_model(), _persona)


@lru_cache
def get_diary() -> Diary:
    """她的日记（data/diary），一天一篇"""
    return Diary(settings.data_dir, lambda: get_model(), _persona)


def get_reviews() -> Reviews:
    """每周回顾（data/reviews），一周一份。每次现取：测试会换数据目录"""
    return Reviews(settings.data_dir, lambda: get_model(), lambda: get_memory_store(), _persona, lambda: settings.language)


@lru_cache
def get_browser() -> BrowserSession:
    """她自己的浏览器窗口（第一次用到才真正打开），配置目录在 data/browser，和用户的浏览器分开"""
    return BrowserSession(settings.data_dir / "browser", make_resolver(http_client))


@lru_cache
def get_registry() -> SkillRegistry:
    search = search_provider()
    log.info("联网搜索：%s", search.name if search else "关掉了（只能读用户给的链接）")
    registry = SkillRegistry([
        web_skill(http_client, search), knowledge_skill(get_knowledge()), code_skill(get_code_projects()),
        browser_skill(), browse_skill(get_browser()), coding_skill(lambda: get_runtime()), reminder_skill(),
        computer_skill(), computer_task_skill(lambda: get_runtime()), x_browser_skill(get_browser()),
        guide_skill(lambda: get_runtime()),
        android_skill(lambda: get_android_session(), android_ready),
        schedule_skill(lambda: get_schedules(), lambda: get_runtime().find_project("")),
        calendar_skill(lambda: get_calendar()), mail_skill(lambda c, u, d: read_mail(c, u, d), lambda: mail_ready()),
    ])
    for skill in _mcp_skills:
        registry.add(skill)
    return registry


# ---------- 设置界面改配置 ----------

LLM_KEYS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
SEARCH_KEYS = ("SEARCH_PROVIDER", "SEARCH_API_KEY")
TTS_KEYS = ("AILA_TTS_ENGINE", "AILA_TTS_SPEAKER", "AILA_TTS_SPEED")
EAR_KEYS = ("AILA_WAKE_THRESHOLD", "AILA_END_SILENCE", "AILA_ASR_LANGUAGE")


def apply(updates: dict[str, str]) -> list[str]:
    """写进 .env，再只重新加载受影响的部分（不用重启服务）。返回重新加载了哪些"""
    global settings
    if not updates:
        return []
    envfile.update(ENV_FILE, updates)
    os.environ.update(updates)
    settings = load_settings()

    reloaded = []
    if "AILA_COMPUTER_INVENTORY" in updates:
        if get_runtime.cache_info().currsize:
            get_runtime().computer_inventory = settings.computer_inventory
        reloaded.append("computer")
    if any(k in updates for k in LLM_KEYS):
        get_model.cache_clear()
        reloaded.append("model")
    if any(k in updates for k in SEARCH_KEYS):
        get_registry.cache_clear()
        reloaded.append("search")
    if any(k.startswith(("AILA_CALENDAR", "AILA_MAIL")) for k in updates):
        reloaded.append("agenda")  # 日历、邮件每次现取设置（get_calendar 按链接换），不用别的
    # 语音模型重新加载要几秒，放到后台；加载好之前还用旧的，新连接用新的
    if any(k in updates for k in TTS_KEYS):
        with _voice_lock:
            _voice.cache_clear()
        threading.Thread(target=get_voice, daemon=True).start()
        reloaded.append("tts")
    if any(k in updates for k in EAR_KEYS):
        with _ears_lock:
            _ears.cache_clear()
        threading.Thread(target=get_ears, daemon=True).start()
        reloaded.append("ears")
    log.info("设置已更新：%s", "、".join(updates))
    return reloaded


class McpRunner:
    """外部 MCP 服务在后台连接，连上一个就把它作为技能包加进来"""

    def __init__(self, hub: McpHub) -> None:
        self.hub = hub
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(hub.serve(self._stop))

    async def stop(self) -> None:
        self._stop.set()
        await self._task


mcp_runner: McpRunner | None = None


def start_mcp(on_skill: Any = None) -> McpRunner | None:
    global mcp_runner
    try:
        # 启用的扩展包带的服务也接进来（停着的 load_config 会跳过）
        configs = load_config(settings.mcp_config, get_extensions().mcp_servers())
    except Exception as err:
        log.warning("mcp.json 读不了：%s", err)
        return None
    if not configs:
        return None

    def add(skill: Skill) -> None:
        _mcp_skills.append(skill)
        get_registry().add(skill)

    mcp_runner = McpRunner(McpHub(configs, on_skill or add))
    return mcp_runner


async def restart_mcp() -> None:
    """mcp.json 改了（设置页加、改、停用、删了服务）：对话这边断开重连，不用重启 AI 服务"""
    global mcp_runner
    old, mcp_runner = mcp_runner, None
    if old is not None:
        await old.stop()
    registry = get_registry()
    for skill in _mcp_skills:
        registry.remove(skill.id)
    _mcp_skills.clear()
    start_mcp()


async def stop_mcp() -> None:
    """服务关的时候：停掉对话这边现在连着的 MCP 服务（可能已经重连过）"""
    if mcp_runner is not None:
        await mcp_runner.stop()


def mcp_status() -> list[dict[str, Any]]:
    """设置界面里显示：配置了哪些 MCP 服务、连上没有"""
    if mcp_runner is None:
        return []
    connected = {s.id for s in _mcp_skills}
    return [
        {"id": f"mcp:{c.id}", "name": c.name, "connected": f"mcp:{c.id}" in connected, "error": mcp_runner.hub.errors.get(c.id)}
        for c in mcp_runner.hub.configs
    ]


# ---------- 手机连接（局域网，见 app/phone） ----------

PHONE_PORT = int(os.getenv("AILA_PHONE_PORT", "8780"))
# 默认开在所有网卡上（局域网里的手机要连）；AILA_PHONE_HOST 可以只开在某一个地址上（比如测试时只开 127.0.0.1）
PHONE_HOST = os.getenv("AILA_PHONE_HOST", "0.0.0.0")  # noqa: S104


@lru_cache
def get_phone() -> "PhoneStore":
    from .phone import PhoneStore

    return PhoneStore.load(settings.data_dir / "phone.json")


@lru_cache
def get_phone_server() -> "PhoneServer":
    from .phone import PhoneServer

    return PhoneServer(PHONE_PORT, PHONE_HOST)


@lru_cache
def get_notifier() -> "Notifier":
    """编程任务的通知（手机页面开着时弹一条，填了推送地址时推到通知栏）"""
    from .notify import Notifier, send_push

    def title_of(sid: str) -> str:
        session = get_runtime().store.get(sid)
        return session.title if session else ""

    return Notifier(lambda: get_phone().enabled, lambda: (get_phone().push_kind, get_phone().push_url), title_of,
                    lambda kind, url, title, text: send_push(http_client, kind, url, title, text))


async def apply_phone() -> None:
    """按开关开、关局域网上的门（启动时、设置页拨开关时）"""
    from .phone.gateway import create_gateway, default_answer, default_chat

    server = get_phone_server()
    if get_phone().enabled:
        await server.start(create_gateway(get_phone(), get_runtime, default_chat, default_answer, get_notifier()))
    else:
        await server.stop()


# ---------- 她操作安卓手机（ADB，见 app/android） ----------

_android_session: "AndroidSession | None" = None


@lru_cache
def get_android_settings() -> "AndroidSettings":
    from .android import AndroidSettings, settings_path

    return AndroidSettings.load(settings_path(settings.data_dir))


def get_adb() -> "Adb | None":
    from .android import Adb, find_adb

    path = find_adb()
    return Adb(path) if path else None


def android_ready() -> bool:
    """对话里给不给她操作手机的工具：开关开着、选好了手机、找得到 ADB（不真的去连手机，要快）"""
    s = get_android_settings()
    if not (s.enabled and s.serial):
        return False
    from .android import find_adb

    return find_adb() is not None


def get_android_session() -> "AndroidSession | None":
    """选好的那台手机（换了手机就换一个；屏幕清单跟着会话走）"""
    global _android_session
    s = get_android_settings()
    adb = get_adb() if s.enabled and s.serial else None
    if adb is None:
        _android_session = None
        return None
    if _android_session is None or _android_session.serial != s.serial or _android_session.adb.path != adb.path:
        from .android import AndroidSession

        _android_session = AndroidSession(adb, s.serial)
    return _android_session


# ---------- 定时和自动触发的编程任务（app/runtime/schedules.py） ----------


@lru_cache
def get_schedules() -> "ScheduleStore":
    from .runtime.schedules import ScheduleStore

    return ScheduleStore(settings.data_dir / "schedules.json")


@lru_cache
def get_scheduler() -> "Scheduler":
    from .runtime.schedules import Scheduler, run_schedule

    async def run_once(s):  # noqa: ANN001, ANN202
        session, result = await run_schedule(get_runtime(), s)
        if not session:  # 没开任务（测试都过了、没开成）：也告诉你一声；开了任务的，做完时任务自己会提醒
            get_notifier().notice("schedule", "", f"定时任务「{s.name}」：{result}", f"定时任务「{s.name}」：{result}")
        return session, result

    return Scheduler(get_schedules(), run_once)


# ---------- 日历和邮件（只读，app/agenda.py、app/mail.py） ----------

_calendar: "Calendar | None" = None


def get_calendar() -> "Calendar | None":
    """填了订阅链接就有；换了链接就换一个（缓存跟着链接走）"""
    global _calendar
    url = settings.calendar_url
    if not url:
        _calendar = None
        return None
    if _calendar is None or _calendar.url != url.replace("webcal://", "https://", 1):
        from .agenda import Calendar

        _calendar = Calendar(url, http_client)
    return _calendar


def mail_ready() -> bool:
    return bool(settings.mail_user and settings.mail_pass)


def read_mail(count: int = 10, unread_only: bool = False, days: int = 7) -> list:  # type: ignore[type-arg]
    from .mail import recent

    return recent(settings.mail_user, settings.mail_pass, settings.mail_host, count, unread_only, days)


@lru_cache
def get_meetings() -> "MeetingWatcher":
    """会前 10 分钟提醒到手机（开了「允许手机连接」才提醒；推出去的不带会议标题）"""
    from .tools.agenda import MeetingWatcher

    return MeetingWatcher(get_calendar, lambda kind, sid, text, push: get_notifier().notice(kind, sid, text, push),
                          lambda: get_phone().enabled)
