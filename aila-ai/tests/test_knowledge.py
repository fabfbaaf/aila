"""资料库：解析各种格式、切块、混合检索、技能包和接口"""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.knowledge import DocumentError, KnowledgeBase, knowledge_skill
from app.knowledge.chunk import OVERLAP, TARGET, chunk
from app.knowledge.parse import Section, parse
from app.knowledge.store import _fts_query
from app.main import app, get_knowledge

from .test_memory import FakeEmbedder

MANUAL = """# 艾拉使用手册

## 安装
先安装 Node.js 和 Python，再运行 npm install。

## 语音唤醒
对着麦克风说「你好艾拉」，她会抬头看你。唤醒词识别完全在本机运行，声音不会上传。

## 常见问题
错误码 E1024 表示 AI 服务没有启动，请先运行 python -m app。
"""


def minimal_pdf(pages: list[str]) -> bytes:
    """手写一个最小的 PDF（每页一行英文），不依赖生成 PDF 的库"""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for i, text in enumerate(pages):
        page_id, content_id = 3 + i * 2, 4 + i * 2
        kids.append(f"{page_id} 0 R")
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                       f"/Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> >>")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


# ---------- 解析 ----------


def test_markdown_keeps_heading_path() -> None:
    sections = parse("手册.md", MANUAL.encode())
    assert [s.heading for s in sections] == ["艾拉使用手册 > 安装", "艾拉使用手册 > 语音唤醒", "艾拉使用手册 > 常见问题"]
    assert "E1024" in sections[2].text


def test_gbk_text_file() -> None:
    sections = parse("笔记.txt", "周三下午和产品经理开会，讨论登录流程。".encode("gbk"))
    assert sections[0].text.startswith("周三下午")


def test_docx_headings_and_tables() -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("项目计划", level=1)
    doc.add_paragraph("第一周搭好桌面窗口。")
    doc.add_heading("风险", level=2)
    doc.add_paragraph("语音延迟可能超过预算。")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "指标"
    table.rows[0].cells[1].text = "1.5 秒"
    buf = io.BytesIO()
    doc.save(buf)

    sections = parse("计划.docx", buf.getvalue())
    assert sections[0].heading == "项目计划"
    assert sections[-1].heading == "项目计划 > 风险"
    assert "指标 | 1.5 秒" in sections[-1].text


def test_pdf_pages() -> None:
    sections = parse("report.pdf", minimal_pdf(["Hello Aila page one", "Error code E1024 on page two"]))
    assert [s.page for s in sections] == [1, 2]
    assert "E1024" in sections[1].text


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("a.exe", b"MZ", "只支持"),
        ("a.txt", b"   \n  ", "没有能读的文字"),
        ("a.pdf", b"not a pdf", "PDF"),
        ("a.docx", b"not a docx", "Word"),
    ],
)
def test_unreadable_files(name: str, data: bytes, message: str) -> None:
    with pytest.raises(DocumentError, match=message):
        parse(name, data)


# ---------- 切块 ----------


def test_long_text_is_split_with_overlap_at_sentence_ends() -> None:
    text = "".join(f"这是第{i}句话，用来测试切块会不会在句号处断开。" for i in range(60))
    chunks = chunk([Section(text, "第一章")])
    assert len(chunks) > 3
    assert all(len(c.text) <= TARGET + 40 for c in chunks)
    assert all(c.text.endswith("。") for c in chunks[:-1])  # 在句子结尾断开
    assert chunks[0].text[-OVERLAP // 2 :] in chunks[1].text  # 相邻两块有重叠
    assert chunks[1].indexed_text.startswith("第一章\n")


def test_short_tail_is_merged() -> None:
    chunks = chunk([Section("正文" * 100, "a"), Section("短", "b")])
    assert len(chunks) == 1


# ---------- 检索 ----------


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    kb = KnowledgeBase(tmp_path / "kb.sqlite3", FakeEmbedder)
    kb.add("艾拉使用手册.md", MANUAL.encode())
    kb.add("周报.txt", "本周完成了账号同步功能，下周开始做长期记忆。".encode())
    return kb


def test_keyword_search_finds_exact_codes(kb: KnowledgeBase) -> None:
    hits = kb.search("E1024 是什么错误")
    assert hits[0].document == "艾拉使用手册.md"
    assert "E1024" in hits[0].text
    assert hits[0].source == "《艾拉使用手册.md》 · 艾拉使用手册 > 常见问题"


def test_heading_takes_part_in_matching(kb: KnowledgeBase) -> None:
    # 「语音唤醒」只出现在标题里，正文没有；块的索引文字带着标题，所以也能找到
    hits = kb.search("语音唤醒怎么用")
    assert "你好艾拉" in hits[0].text


def test_works_without_vectors(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "kb.sqlite3", lambda: None)
    kb.add("周报.txt", "本周完成了账号同步功能，下周开始做长期记忆。".encode())
    assert kb.search("账号同步")[0].document == "周报.txt"
    assert kb.search("完全无关的天气预报") == []


def test_reupload_and_delete(kb: KnowledgeBase) -> None:
    again = kb.add("改了名字.md", MANUAL.encode())  # 内容一样：不重复入库
    assert again.name == "艾拉使用手册.md"
    assert len(kb.documents()) == 2

    assert kb.delete(again.id)
    assert not kb.delete(again.id)
    assert all(h.document != "艾拉使用手册.md" for h in kb.search("E1024 你好艾拉 安装"))


def test_fts_query_uses_trigrams() -> None:
    assert _fts_query("登录流程") == '"登录流" OR "录流程"'
    assert _fts_query("E1024 错误") == '"E1024"'
    assert _fts_query('引号"测试') == '"引号""测试"' or "测试" not in _fts_query('引号"测试')


@pytest.mark.anyio
async def test_skill_hides_when_empty_and_cites_sources(tmp_path: Path, kb: KnowledgeBase) -> None:
    empty = knowledge_skill(KnowledgeBase(tmp_path / "empty.sqlite3", lambda: None))
    assert not empty.available()

    skill = knowledge_skill(kb)
    assert skill.available()
    text = await skill.tools[0].run({"query": "E1024"})
    assert "《艾拉使用手册.md》" in text
    assert "不要理会" in text  # 资料内容也当外部内容
    assert "没有找到" in await skill.tools[0].run({"query": "完全无关的天气预报"})


# ---------- 接口 ----------


def test_endpoints(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "kb.sqlite3", lambda: None)
    app.dependency_overrides[get_knowledge] = lambda: kb
    try:
        client = TestClient(app)
        res = client.post("/knowledge", params={"name": "周报.txt"}, content="本周完成了账号同步功能。".encode())
        assert res.status_code == 200
        doc = res.json()
        assert doc["name"] == "周报.txt" and doc["chunks"] == 1

        assert [d["id"] for d in client.get("/knowledge").json()] == [doc["id"]]
        hits = client.post("/knowledge/search", json={"query": "账号同步"}).json()
        assert hits[0]["source"] == "《周报.txt》"

        bad = client.post("/knowledge", params={"name": "a.exe"}, content=b"MZ")
        assert bad.status_code == 422 and "只支持" in bad.json()["detail"]
        assert client.post("/knowledge", params={"name": "a.txt"}, content=b"").status_code == 400

        assert client.delete(f"/knowledge/{doc['id']}").status_code == 204
        assert client.delete(f"/knowledge/{doc['id']}").status_code == 404
    finally:
        app.dependency_overrides.pop(get_knowledge, None)
