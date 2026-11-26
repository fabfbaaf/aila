r"""把 AI 服务打成一个目录：aila-ai/dist/aila-ai/aila-ai.exe（安装包里带它，用户不用装 Python）。

用法（在 aila-ai 目录里）：
    .venv\Scripts\pip install -r requirements-dev.txt     # 里面有 PyInstaller
    .venv\Scripts\python scripts\build_service.py

为什么用 --onedir 不用 --onefile：onefile 每次启动都要先解压到临时目录（慢好几秒），杀毒软件也更容易误报。

打好以后自己冒烟测试，两步，有一步不过就算失败：
1. aila-ai.exe --self-check：各个库和它们自带的数据文件（语法、文字识别模型、网页提取的配置……）都实际加载一遍
2. 起服务（端口、数据目录都指向临时目录），等 /health 返回 ok，再关掉
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
OUT = DIST / "aila-ai"
EXE = OUT / "aila-ai.exe"
SMOKE_PORT = 8799

# 库自带的数据文件和用到时才导入的模块。PyInstaller 只看得见直接 import 的代码，下面这些都是试出来的：
COLLECT_ALL = [
    # 语音：唤醒、识别、合成都在它的原生库里，带 DLL
    "sherpa_onnx",
    # 看屏幕的文字识别：模型（.onnx）和 config.yaml 跟着包走
    "rapidocr_onnxruntime",
    # 文字识别和向量模型都靠它跑，原生库
    "onnxruntime",
    # 向量模型：模型列表等数据文件
    "fastembed",
    # 网页正文提取：settings.cfg；它用到的 justext 有各语言的停用词表、htmldate/courlan 有数据文件
    "trafilatura",
    "justext",
    "htmldate",
    "courlan",
    # 她的浏览器：只要驱动（driver/ 里的 node 和 package），不打包浏览器，用本机的 Edge / Chrome
    "playwright",
]
COLLECT_SUBMODULES = [
    # 我们自己的代码：uvicorn 按字符串 "app.main:app" 导入，路由、工具也有按需导入的
    "app",
    # uvicorn 按名字挑选事件循环、HTTP、WebSocket 的实现
    "uvicorn",
    # 代码库按语言导入语法（app/code/parse.py 里按需 import）
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_java",
    # 外部工具（MCP）：客户端的各种连接方式按需导入。不用 --collect-all mcp：mcp.cli 没装 typer 时一导入就退出
    "mcp.client",
    "mcp.shared",
    "mcp.os",
    "mcp.types",
]
# 打包后用不到的大库：别的库「可能会用」的可选依赖，PyInstaller 会顺着 import 把它们拉进来
EXCLUDES = ["tkinter", "matplotlib", "IPython", "pytest", "PIL.ImageQt", "PyQt5", "PySide6"]


def build() -> None:
    args = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "packaging" / "entry.py"),
        "--name", "aila-ai",
        "--onedir",
        "--console",  # 桌面端启动时会隐藏窗口；控制台程序才能把日志写到 stdout 给桌面端看
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT),
        # 第一次在设置页保存密钥时，app/envfile.py 照着它建 .env（注释都在）
        "--add-data", f"{ROOT / '.env.example'}{os.pathsep}.",
        # 使用说明（她查自己的功能用，app/guide）
        "--add-data", f"{ROOT / 'app' / 'guide' / 'guide.md'}{os.pathsep}app/guide",
        # 手机上打开的页面（app/phone/static：局域网的门直接发这两个文件）
        "--add-data", f"{ROOT / 'app' / 'phone' / 'static'}{os.pathsep}app/phone/static",
    ]
    for name in COLLECT_ALL:
        args += ["--collect-all", name]
    for name in COLLECT_SUBMODULES:
        args += ["--collect-submodules", name]
    for name in EXCLUDES:
        args += ["--exclude-module", name]
    print("打包：", " ".join(args[3:6]), "……", flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def isolated_env(tmp: Path, port: int = SMOKE_PORT) -> dict[str, str]:
    """冒烟测试用的环境：所有文件都在临时目录里，不读你的 .env，没有密钥（用假模型）"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AILA_", "LLM_"))}
    env.update({
        "AILA_ENV_FILE": str(tmp / ".env"),
        "AILA_DATA_DIR": str(tmp / "data"),
        "AILA_MODELS_DIR": str(tmp / "models"),
        "AILA_MCP_CONFIG": str(tmp / "mcp.json"),
        "AILA_HOST": "127.0.0.1",
        "AILA_PORT": str(port),
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def self_check(env: dict[str, str]) -> None:
    print("自检 ……", flush=True)
    subprocess.run([str(EXE), "--self-check"], env=env, check=True, timeout=300)


def smoke(env: dict[str, str]) -> float:
    """起服务，等 /health 返回 ok，返回用了几秒"""
    print("起服务 ……", flush=True)
    start = time.monotonic()
    proc = subprocess.Popen([str(EXE)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        while time.monotonic() - start < 120:
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                raise RuntimeError(f"服务退出了（{proc.returncode}）：\n{out[-3000:]}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{env['AILA_PORT']}/health", timeout=2) as r:
                    data = json.loads(r.read())
                if data.get("ok"):
                    return time.monotonic() - start
            except OSError:
                pass
            time.sleep(0.5)
        raise RuntimeError("120 秒内 /health 没有返回 ok")
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=10)


def size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def main() -> int:
    if sys.platform != "win32":
        print("现在只做了 Windows 的安装包", file=sys.stderr)
        return 1
    if "--skip-build" not in sys.argv:
        shutil.rmtree(OUT, ignore_errors=True)
        build()
    tmp = Path(tempfile.mkdtemp(prefix="aila-build-smoke-"))
    try:
        env = isolated_env(tmp)
        self_check(env)
        seconds = smoke(env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n打好了：{EXE}\n目录 {size_mb(OUT):.0f} MB，冒烟测试 /health 在 {seconds:.1f} 秒后返回 ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
