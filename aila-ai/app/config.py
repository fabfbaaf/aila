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
    )
