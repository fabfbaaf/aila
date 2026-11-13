"""把上传的文件变成文字。每段文字带上页码（PDF）或标题路径（Markdown、Word），回答时能说出出处。"""

import io
import re
from dataclasses import dataclass
from pathlib import PurePath

MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 2_000_000

SUPPORTED = (".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm")


class DocumentError(Exception):
    """文件读不了，消息直接给用户看"""


@dataclass
class Section:
    text: str
    heading: str = ""  # 「第三章 > 3.2 登录」
    page: int | None = None


def parse(name: str, data: bytes) -> list[Section]:
    ext = PurePath(name).suffix.lower()
    if ext not in SUPPORTED:
        raise DocumentError(f"暂时只支持 {' '.join(SUPPORTED)} 这些格式")
    if len(data) > MAX_BYTES:
        raise DocumentError("文件太大了（最多 20MB）")
    if ext == ".pdf":
        sections = _pdf(data)
    elif ext == ".docx":
        sections = _docx(data)
    elif ext in (".html", ".htm"):
        sections = _html(data)
    else:
        sections = _markdown(_decode(data))
    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise DocumentError("这个文件里没有能读的文字（扫描版 PDF 要先做文字识别）")
    total = sum(len(s.text) for s in sections)
    if total > MAX_CHARS:
        raise DocumentError("文件里的字太多了（最多 200 万字）")
    return sections


def _decode(data: bytes) -> str:
    """中文文本文件常见 UTF-8 和 GBK，猜一下"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()
    return str(best) if best else data.decode("utf-8", errors="replace")


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _markdown(text: str) -> list[Section]:
    """按标题分段；没有标题的纯文本就是一整段"""
    sections: list[Section] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            sections.append(Section("\n".join(buf).strip(), " > ".join(path)))
            buf.clear()

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = path[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return sections


def _pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("这个 PDF 有密码，打不开")
        return [Section(page.extract_text() or "", page=i + 1) for i, page in enumerate(reader.pages)]
    except PdfReadError as err:
        raise DocumentError("PDF 文件坏了，读不了") from err


def _docx(data: bytes) -> list[Section]:
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as err:
        raise DocumentError("Word 文件读不了（只支持 .docx，不支持老的 .doc）") from err
    sections: list[Section] = []
    path: list[str] = []
    buf: list[str] = []
    for p in doc.paragraphs:
        style = (p.style.name if p.style is not None else "") or ""
        m = re.match(r"Heading (\d)|标题 ?(\d)", style)
        if m and p.text.strip():
            if buf:
                sections.append(Section("\n".join(buf), " > ".join(path)))
                buf = []
            level = int(m.group(1) or m.group(2))
            path = path[: level - 1] + [p.text.strip()]
        elif p.text.strip():
            buf.append(p.text)
    for table in doc.tables:
        for row in table.rows:
            buf.append(" | ".join(c.text.strip() for c in row.cells))
    if buf:
        sections.append(Section("\n".join(buf), " > ".join(path)))
    return sections


def _html(data: bytes) -> list[Section]:
    import trafilatura

    text = trafilatura.extract(trafilatura.load_html(data), output_format="markdown", include_tables=True) or ""
    return _markdown(text)
