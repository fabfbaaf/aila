"""配置：从环境变量和 aila-ai/.env 读取"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# 一般就是 aila-ai/.env；同一台电脑跑两份服务（或测试）时可以指定别的文件
ENV_FILE = Path(os.getenv("AILA_ENV_FILE", str(ROOT / ".env")))
load_dotenv(ENV_FILE)


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
    data_dir: Path
    mcp_config: Path
    # 编程 Agent：用的模型（默认和对话一样）、上下文窗口（快满时压缩）、一轮最多几步
    coder_model: str = ""
    # 派出去查资料的子 Agent 用的模型（便宜、快的）；空 = 和 coder_model 一样
    coder_fast_model: str = ""
    coder_context: int | None = None  # 只在用户明确配置时覆盖模型窗口
    coder_max_steps: int = 60

    @property
    def mock(self) -> bool:
        """没填密钥时用假模型，整条链路照样能跑通"""
        return not self.llm_api_key


def load_settings() -> Settings:
    coder_context = os.getenv("AILA_CODER_CONTEXT")
    return Settings(
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "deepseek-flash"),
        search_provider=os.getenv("SEARCH_PROVIDER", "bocha").strip().lower(),
        search_api_key=os.getenv("SEARCH_API_KEY", "").strip(),
        host=os.getenv("AILA_HOST", "127.0.0.1"),
        port=int(os.getenv("AILA_PORT", "8765")),
        max_history=int(os.getenv("AILA_MAX_HISTORY", "40")),
        voice=os.getenv("AILA_VOICE", "on").strip().lower() not in ("off", "0", "false"),
        models_dir=Path(os.getenv("AILA_MODELS_DIR", str(ROOT / "models"))),
        tts_engine=os.getenv("AILA_TTS_ENGINE", "melo").strip().lower(),
        tts_speaker=int(os.getenv("AILA_TTS_SPEAKER", "0")),
        tts_speed=float(os.getenv("AILA_TTS_SPEED", "1.0")),
        wake_threshold=float(os.getenv("AILA_WAKE_THRESHOLD", "0.25")),
        end_silence=float(os.getenv("AILA_END_SILENCE", "0.4")),
        asr_language=os.getenv("AILA_ASR_LANGUAGE", "zh").strip().lower(),
        # 知识库等本机数据（不进 Git）
        data_dir=Path(os.getenv("AILA_DATA_DIR", str(ROOT / "data"))),
        # 外部 MCP 服务的配置，参考 mcp.example.json
        mcp_config=Path(os.getenv("AILA_MCP_CONFIG", str(ROOT / "mcp.json"))),
        coder_model=os.getenv("AILA_CODER_MODEL", "").strip(),
        coder_fast_model=os.getenv("AILA_CODER_FAST_MODEL", "").strip(),
        coder_context=int(coder_context) if coder_context is not None else None,
        coder_max_steps=int(os.getenv("AILA_CODER_MAX_STEPS", "60")),
    )
