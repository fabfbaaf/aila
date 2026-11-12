"""配置：从环境变量和 aila-ai/.env 读取"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    search_provider: str
    search_api_key: str
    host: str
    port: int
    max_history: int
    voice: bool
    models_dir: Path
    tts_engine: str
    tts_speaker: int
    tts_speed: float
    wake_threshold: float
    end_silence: float
    asr_language: str

    @property
    def mock(self) -> bool:
        """没填密钥时用假模型，整条链路照样能跑通"""
        return not self.llm_api_key


def load_settings() -> Settings:
    return Settings(
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        search_provider=os.getenv("SEARCH_PROVIDER", "bocha").strip().lower(),
        search_api_key=os.getenv("SEARCH_API_KEY", "").strip(),
        host=os.getenv("AILA_HOST", "127.0.0.1"),
        port=int(os.getenv("AILA_PORT", "8765")),
        max_history=int(os.getenv("AILA_MAX_HISTORY", "20")),
        voice=os.getenv("AILA_VOICE", "on").strip().lower() not in ("off", "0", "false"),
        models_dir=Path(os.getenv("AILA_MODELS_DIR", str(Path(__file__).resolve().parent.parent / "models"))),
        tts_engine=os.getenv("AILA_TTS_ENGINE", "melo").strip().lower(),
        tts_speaker=int(os.getenv("AILA_TTS_SPEAKER", "0")),
        tts_speed=float(os.getenv("AILA_TTS_SPEED", "1.0")),
        wake_threshold=float(os.getenv("AILA_WAKE_THRESHOLD", "0.25")),
        end_silence=float(os.getenv("AILA_END_SILENCE", "0.4")),
        asr_language=os.getenv("AILA_ASR_LANGUAGE", "zh").strip().lower(),
    )
