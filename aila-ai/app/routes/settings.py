"""设置：桌面端的设置窗口读写 AI 服务的配置（大模型、搜索、语音）。

改动写回 aila-ai/.env，并当场生效（只重新加载受影响的部分）。密钥只能写不能读：
读的时候只告诉「设没设」，免得密钥出现在界面、日志或别的程序里。
"""

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import deps
from ..deps import get_model, mcp_status, status
from ..llm import ChatModel, classify_error
from ..models_download import missing as missing_models
from ..persona import STYLES

router = APIRouter(prefix="/settings")

TTS_ENGINES = [
    {"id": "melo", "name": "MeloTTS（中英混读，反应快）", "dirs": ["vits-melo-tts-zh_en"]},
    {"id": "kokoro", "name": "Kokoro（音色多，慢一倍）", "dirs": ["kokoro-multi-lang-v1_1", "kokoro-int8-multi-lang-v1_1"]},
    {"id": "matcha", "name": "Matcha（最快，只会中文）", "dirs": ["matcha-icefall-zh-baker"]},
]


def _engines() -> list[dict[str, Any]]:
    models = deps.settings.models_dir
    return [{"id": e["id"], "name": e["name"], "installed": any((models / d).exists() for d in e["dirs"])} for e in TTS_ENGINES]


@router.get("")
def read() -> dict[str, Any]:
    s = deps.settings
    return {
        "llm": {"baseUrl": s.llm_base_url, "model": s.llm_model, "apiKeySet": bool(s.llm_api_key),
                "coderModel": s.coder_model, "coderFastModel": s.coder_fast_model},
        "search": {"provider": s.search_provider if s.search_provider in ("bocha", "tavily", "none") else "bocha",
                   "apiKeySet": bool(s.search_api_key)},
        "computer": {"inventory": s.computer_inventory},
        # 订阅链接、授权码都不回显，只说填了没有；邮箱地址显示出来（你要认得是哪个邮箱）
        "agenda": {"calendarSet": bool(s.calendar_url), "mailUser": s.mail_user, "mailPassSet": bool(s.mail_pass),
                   "mailHost": s.mail_host},
        "persona": {"style": s.persona_style if s.persona_style in STYLES else "gentle", "call": s.persona_call,
                    "catchphrase": s.persona_catchphrase},
        "language": s.language,
        "voice": {
            "enabled": s.voice,
            "ttsEngine": s.tts_engine,
            "ttsEngines": _engines(),
            "ttsSpeaker": s.tts_speaker,
            "ttsSpeed": s.tts_speed,
            "wakeThreshold": s.wake_threshold,
            "endSilence": s.end_silence,
            "asrLanguage": s.asr_language,
            # 默认要的语音模型里还没下载的（安装包不带模型，设置页据此显示「下载语音模型」）
            "modelsMissing": missing_models(s.models_dir),
        },
        "status": dict(status),
        "mcp": mcp_status(),
        "paths": {"env": str(deps.ENV_FILE), "models": str(s.models_dir), "data": str(s.data_dir), "mcp": str(s.mcp_config)},
    }


class SettingsUpdate(BaseModel):
    """只传要改的项。密钥传空字符串表示清掉"""

    llmBaseUrl: str | None = Field(default=None, max_length=300)
    llmModel: str | None = Field(default=None, min_length=1, max_length=100)
    llmApiKey: str | None = Field(default=None, max_length=300)
    # 编程 Agent 用的模型；空字符串 = 和对话一样
    coderModel: str | None = Field(default=None, max_length=100)
    # 查资料的子 Agent 用的模型；空字符串 = 和编程用的一样
    coderFastModel: str | None = Field(default=None, max_length=100)
    computerInventory: bool | None = Field(default=None, strict=True)
    # 性格：风格、怎么称呼你、口头禅（空字符串 = 不用）
    personaStyle: Literal["gentle", "lively", "calm", "teasing"] | None = None
    personaCall: str | None = Field(default=None, max_length=12)
    personaCatchphrase: str | None = Field(default=None, max_length=12)
    language: Literal["zh", "en"] | None = None
    searchProvider: Literal["bocha", "tavily", "none"] | None = None
    searchApiKey: str | None = Field(default=None, max_length=300)
    ttsEngine: Literal["melo", "kokoro", "matcha"] | None = None
    ttsSpeaker: int | None = Field(default=None, ge=0, le=200)
    ttsSpeed: float | None = Field(default=None, ge=0.5, le=2.0)
    wakeThreshold: float | None = Field(default=None, ge=0.05, le=0.6)
    endSilence: float | None = Field(default=None, ge=0.2, le=1.5)
    asrLanguage: Literal["zh", "auto"] | None = None
    # 日历订阅链接、邮箱、授权码、IMAP 服务器（空字符串 = 清掉）
    calendarUrl: str | None = Field(default=None, max_length=2000)
    mailUser: str | None = Field(default=None, max_length=200)
    mailPass: str | None = Field(default=None, max_length=200)
    mailHost: str | None = Field(default=None, max_length=200)

    @field_validator("calendarUrl")
    @classmethod
    def _calendar(cls, v: str | None) -> str | None:
        if v and not v.strip().startswith(("https://", "http://", "webcal://")):
            raise ValueError("日历订阅链接要以 https:// 或 webcal:// 开头")
        return v.strip() if v is not None else v

    @field_validator("mailUser")
    @classmethod
    def _mail(cls, v: str | None) -> str | None:
        if v and "@" not in v:
            raise ValueError("邮箱地址不对")
        return v.strip() if v is not None else v

    @field_validator("mailPass", "mailHost")
    @classmethod
    def _plain(cls, v: str | None) -> str | None:
        if v is not None and any(c.isspace() for c in v.strip()):
            raise ValueError("里面不应该有空格")
        return v.strip() if v is not None else v

    @field_validator("llmBaseUrl")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("地址要以 http:// 或 https:// 开头")
        return v.rstrip("/") if v else v

    @field_validator("personaCall", "personaCatchphrase")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        if v is not None and any(not ch.isprintable() for ch in v):
            raise ValueError("不能有换行或控制字符")
        return v.strip() if v is not None else v

    @field_validator("llmApiKey", "searchApiKey")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is not None and any(c.isspace() for c in v.strip()):
            raise ValueError("密钥里不应该有空格")
        return v.strip() if v is not None else v


ENV_NAMES = {
    "llmBaseUrl": "LLM_BASE_URL", "llmModel": "LLM_MODEL", "llmApiKey": "LLM_API_KEY",
    "coderModel": "AILA_CODER_MODEL", "coderFastModel": "AILA_CODER_FAST_MODEL",
    "computerInventory": "AILA_COMPUTER_INVENTORY",
    "personaStyle": "AILA_PERSONA_STYLE", "personaCall": "AILA_PERSONA_CALL", "personaCatchphrase": "AILA_PERSONA_CATCHPHRASE", "language": "AILA_LANGUAGE",
    "searchProvider": "SEARCH_PROVIDER", "searchApiKey": "SEARCH_API_KEY",
    "calendarUrl": "AILA_CALENDAR_URL", "mailUser": "AILA_MAIL_USER", "mailPass": "AILA_MAIL_PASS", "mailHost": "AILA_MAIL_HOST",
    "ttsEngine": "AILA_TTS_ENGINE", "ttsSpeaker": "AILA_TTS_SPEAKER", "ttsSpeed": "AILA_TTS_SPEED",
    "wakeThreshold": "AILA_WAKE_THRESHOLD", "endSilence": "AILA_END_SILENCE", "asrLanguage": "AILA_ASR_LANGUAGE",
}


@router.put("")
def write(update: SettingsUpdate) -> dict[str, Any]:
    changes = update.model_dump(exclude_none=True)
    if "ttsEngine" in changes and not next(e for e in _engines() if e["id"] == changes["ttsEngine"])["installed"]:
        raise HTTPException(status_code=422, detail="这个语音引擎的模型还没下载（scripts/download_models.py）")
    reloaded = deps.apply({ENV_NAMES[k]: str(v) for k, v in changes.items()})
    return {"reloaded": reloaded, "settings": read()}


@router.post("/test")
async def test_llm(model: ChatModel = Depends(get_model)) -> dict[str, Any]:
    """测试大模型能不能连上：密钥、地址、模型名都对才算通"""
    if not hasattr(model, "ping"):
        return {"ok": False, "message": "还没有填大模型密钥，现在用的是内置的假模型"}
    t0 = time.perf_counter()
    try:
        await model.ping()
    except Exception as err:
        _, message = classify_error(err)
        return {"ok": False, "message": f"{message}（{type(err).__name__}）"}
    return {"ok": True, "message": f"连上了，用时 {round((time.perf_counter() - t0) * 1000)} 毫秒"}
