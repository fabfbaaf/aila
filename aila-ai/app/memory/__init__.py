"""长期记忆：回想（挑出相关的放进提示词）、提取（从对话里更新记忆）、记忆库（store.py）和经历（episodes.py）。
关于你的事由桌面端保存、经云端在设备之间同步，记忆库里是它的副本（多存了向量）；
对话摘要、编程任务的经历只存在本机的记忆库里，不同步。
"""

from .embedder import Embedder, FastEmbedder
from .extract import Turn, extract
from .recall import Memory, describe, recall

__all__ = ["Embedder", "FastEmbedder", "Memory", "Turn", "describe", "extract", "recall"]
