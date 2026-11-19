"""PyInstaller 的入口：打包后的 aila-ai.exe 就是 python -m app。

app/__main__.py 用的是相对导入，不能直接当入口脚本，所以另写这一个。
"""

import multiprocessing
import sys

from app.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # 打包后有库用到多进程时，子进程不会再启动一遍服务
    # 打包后的程序不认 PYTHONIOENCODING：输出被桌面端接管道读时默认是 cp1252，一打汉字就报错。统一用 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
