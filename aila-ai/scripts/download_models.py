r"""下载语音模型到 aila-ai/models/（不进 Git）。都来自 sherpa-onnx 官方发布页。

用法：
    .venv\Scripts\python scripts\download_models.py              # 默认：语音四个 + 向量模型，约 460MB
    .venv\Scripts\python scripts\download_models.py tts-kokoro   # 可选的合成引擎

下载逻辑在 app/models_download.py（安装包里用 aila-ai.exe --download-models 调同一个函数）。
模型放在 AILA_MODELS_DIR，没设就是 aila-ai/models/。已经下载过的会跳过。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models_download import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
