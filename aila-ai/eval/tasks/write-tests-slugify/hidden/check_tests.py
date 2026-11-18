"""检查她写的测试：真的实现要全过；每个故意写错的实现（mutants/）都要被至少一个测试抓住。"""

import shutil
import subprocess
import sys
from pathlib import Path


def run():
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_slug.py"], capture_output=True, text=True).returncode


if not Path("test_slug.py").exists():
    sys.exit("没有 test_slug.py")
if run() != 0:
    sys.exit("真的实现过不了她写的测试")
original = Path("slug.py").read_text(encoding="utf-8")
escaped = []
try:
    for mutant in sorted(Path("mutants").glob("*.py")):
        shutil.copyfile(mutant, "slug.py")
        if run() == 0:
            escaped.append(mutant.stem)
finally:
    Path("slug.py").write_text(original, encoding="utf-8")
if escaped:
    sys.exit("这些写错的实现没被抓住：" + "、".join(escaped))
print("测试抓住了所有写错的实现")
