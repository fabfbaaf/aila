"""打包后的自检：aila-ai.exe --self-check

为什么要它：PyInstaller 只看得见直接 import 的代码，库自带的数据文件（模型、语法、配置）和
用到时才导入的库很容易漏掉，漏了也要等用户用到那个功能才报错。这里把这些都实际加载一遍，
build_service.py 打好包以后跑它，有一项不行就算打包失败。不连网、不下载模型。
"""

import sys
from collections.abc import Callable


def _code() -> None:
    from .code.parse import parse_file

    samples = {
        "a.py": b"def f():\n    return 1\n",
        "a.js": b"function f() { return 1 }\n",
        "a.ts": b"export function f(): number { return 1 }\n",
        "a.tsx": b"export function F() { return <div/> }\n",
        "A.java": b"class A { int f() { return 1; } }\n",
    }
    for name, src in samples.items():
        if not parse_file(name, src):
            raise RuntimeError(f"{name} 没解析出函数")


def _knowledge() -> None:
    import io

    from docx import Document

    from .knowledge.parse import parse

    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph("你好")
    doc.save(buf)
    if not parse("a.docx", buf.getvalue()):
        raise RuntimeError("Word 没解析出内容")
    html = "<html><body><article><h1>标题</h1>" + "<p>这是一段正文，用来测试网页正文提取。</p>" * 20 + "</article></body></html>"
    if not parse("a.html", html.encode()):
        raise RuntimeError("网页没解析出内容")
    import pypdf  # noqa: F401


def _ocr() -> None:
    from .screen.ocr import Ocr

    Ocr()  # 加载安装包自带的文字识别模型


def _voice() -> None:
    import sherpa_onnx  # noqa: F401


def _embed() -> None:
    import fastembed  # noqa: F401


def _browser() -> None:
    from pathlib import Path

    import playwright

    driver = Path(playwright.__file__).parent / "driver"
    if not any(driver.glob("node*")):
        raise RuntimeError(f"没有 Playwright 驱动：{driver}")


def _screen() -> None:
    import mss  # noqa: F401


def _mcp() -> None:
    import mcp  # noqa: F401


def _app() -> None:
    import app.main  # noqa: F401 —— uvicorn 按字符串导入它


CHECKS: dict[str, Callable[[], None]] = {
    "服务": _app,
    "代码解析": _code,
    "资料解析": _knowledge,
    "文字识别": _ocr,
    "语音": _voice,
    "向量": _embed,
    "浏览器驱动": _browser,
    "截屏": _screen,
    "外部工具": _mcp,
}


def main() -> int:
    failed = 0
    for name, check in CHECKS.items():
        try:
            check()
            print(f"通过  {name}", flush=True)
        except Exception as e:  # noqa: BLE001 —— 每一项都要跑到，最后汇总
            failed += 1
            print(f"失败  {name}：{type(e).__name__}: {e}", file=sys.stderr, flush=True)
    print("自检通过" if not failed else f"自检有 {failed} 项没过", flush=True)
    return 1 if failed else 0
