"""切块：检索的单位。太大了找到的不准、塞给大模型也浪费；太小了丢上下文。

- 每块 500 字左右，尽量在段落、句号处断开，不把一句话切成两半
- 相邻两块重叠 80 字：答案正好跨在边界上时也能完整找到
- 每块记下所在的标题路径和页码，检索时标题也参与匹配（「第三章 登录」里的内容，问「登录」能找到）
"""

import re
from dataclasses import dataclass

from .parse import Section

TARGET = 500
OVERLAP = 80
MIN_CHUNK = 20


@dataclass
class Chunk:
    text: str
    heading: str = ""
    page: int | None = None

    @property
    def indexed_text(self) -> str:
        """用来算向量和关键词索引的文字：带上标题，脱离上下文也看得懂"""
        return f"{self.heading}\n{self.text}" if self.heading else self.text


_BREAKS = re.compile(r"(?<=[。！？!?；;\n])")


def chunk(sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for s in sections:
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", s.text)).strip()
        for piece in _split(text):
            if len(piece) >= MIN_CHUNK or not chunks:
                chunks.append(Chunk(piece, s.heading, s.page))
            else:
                chunks[-1].text += "\n" + piece  # 太短的尾巴并到上一块
    return chunks


def _split(text: str) -> list[str]:
    if len(text) <= TARGET:
        return [text] if text else []
    sentences = [s for s in _BREAKS.split(text) if s]
    pieces: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > TARGET:  # 超长的一句（没有标点的大段文字）硬切
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(s[:TARGET])
            s = s[TARGET - OVERLAP :]
        if len(cur) + len(s) > TARGET and cur:
            pieces.append(cur)
            cur = cur[-OVERLAP:]  # 下一块从上一块的结尾开始，留一段重叠
        cur += s
    if cur.strip():
        pieces.append(cur)
    return [p.strip() for p in pieces if p.strip()]
