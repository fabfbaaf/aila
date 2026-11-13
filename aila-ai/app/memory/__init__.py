"""长期记忆：回想（挑出相关的放进提示词）和提取（从对话里更新记忆）。
记忆本身由桌面端保存、经云端在设备之间同步；这里只负责「算」，不存用户数据。
"""

from .embedder import Embedder, FastEmbedder
from .extract import Turn, extract
from .recall import Memory, describe, recall

__all__ = ["Embedder", "FastEmbedder", "Memory", "Turn", "describe", "extract", "recall"]
