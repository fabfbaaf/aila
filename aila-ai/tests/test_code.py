"""代码库：按函数切块、调用关系、堆栈解析、增量索引、检索、按报错定位、工具和接口"""

import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.code import CodeIndex, CodeProjects, parse_file, parse_frames
from app.main import app
from app.tools.code import code_skill

from .test_chat import parse_sse

PY = '''"""购物车：算总价。"""

TAX = 0.1


def price_of(item):
    """查单价"""
    return item.get("price")


class Cart:
    """购物车里的东西"""

    def __init__(self):
        self.items = []

    def total(self):
        # 总价 = 单价 × 数量
        return sum(price_of(i) * i["count"] for i in self.items)

    @staticmethod
    def empty():
        def inner():
            return Cart()
        return inner()
'''

TS = """// 购物车界面
export interface Item { price: number }

export class CartView {
  render(): void {
    this.total()
    format(1)
  }
  total = () => sum([])
}

export const format = (n: number) => n.toFixed(2)
function sum(xs: number[]) { return xs.length }
"""

JAVA = """package com.shop.cart;

/** 结账服务 */
public class CheckoutService {
    public CheckoutService() {}

    /** 下单 */
    public long checkout(long userId) {
        lockUser(userId);
        return new Receipt().id();
    }

    private void lockUser(long userId) {}
}
"""

VUE = """<template>
  <!-- 购物车页面 -->
  <div><CartList /><button @click="pay">结账</button></div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
const count = ref(0)
function pay() {
  count.value++
}
</script>
"""


CHECKOUT = '''from shop.cart import Cart, price_of


def checkout(items):
    cart = Cart()
    return cart.total() + price_of(items[0])
'''


def make_project(root: Path) -> Path:
    files = {
        "shop/cart.py": PY,
        "shop/checkout.py": CHECKOUT,
        "web/src/CartView.ts": TS,
        "server/src/main/java/com/shop/cart/CheckoutService.java": JAVA,
        "web/src/Cart.vue": VUE,
        "node_modules/lib/index.js": "function hidden() {}",  # 第三方库：不进索引
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


class FakeEmbedder:
    """按字统计的假向量：意思相近不行，但能让向量这一路跑起来"""

    dim = 64

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, np.float32)
        for ch in text:
            v[ord(ch) % self.dim] += 1  # 不用 hash()：每次运行结果都一样
        return v / (np.linalg.norm(v) or 1)

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


@pytest.fixture
def index(tmp_path: Path) -> CodeIndex:
    idx = CodeIndex(make_project(tmp_path / "proj"), tmp_path / "code.sqlite3", lambda: FakeEmbedder())  # type: ignore[arg-type,return-value]
    idx.refresh()
    return idx


# ---------- 切块 ----------


def test_python_chunks() -> None:
    chunks = {c.qname: c for c in parse_file("shop/cart.py", PY.encode())}
    assert set(chunks) == {"cart", "price_of", "Cart", "Cart.__init__", "Cart.total", "Cart.empty", "Cart.empty.inner"}
    assert chunks["cart"].kind == "module" and "TAX = 0.1" in chunks["cart"].text
    assert chunks["price_of"].doc == "查单价" and chunks["price_of"].start == 6
    assert chunks["Cart.total"].kind == "method" and chunks["Cart.total"].signature == "def total(self)"
    assert chunks["Cart.empty"].start == 21  # 从装饰器那行算起
    assert chunks["Cart.empty.inner"].kind == "function"
    # 直接调用记名字，对象上的方法调用前面加点
    assert "price_of" in chunks["Cart.total"].calls and "sum" in chunks["Cart.total"].calls
    assert ".get" in chunks["price_of"].calls
    # 类这一块：类头 + 方法签名，不重复放方法体
    assert "def total(self)" in chunks["Cart"].text and "sum(" not in chunks["Cart"].text
    # 每块都知道所在文件是干什么的
    assert chunks["Cart.total"].context.startswith("购物车：算总价。")


def test_typescript_java_vue_chunks() -> None:
    ts = {c.qname: c for c in parse_file("CartView.ts", TS.encode())}
    assert {"Item", "CartView", "CartView.render", "CartView.total", "format", "sum"} <= set(ts)
    assert "total" in ts["CartView.render"].calls  # this.total() 当作同一个类里的直接调用
    assert "format" in ts["CartView.render"].calls

    java = {c.qname: c for c in parse_file("CheckoutService.java", JAVA.encode())}
    assert java["CheckoutService.checkout"].doc == "下单"
    assert "lockUser" in java["CheckoutService.checkout"].calls and "Receipt" in java["CheckoutService.checkout"].calls

    vue = {c.qname: c for c in parse_file("web/src/Cart.vue", VUE.encode())}
    assert vue["pay"].start == 9  # <script> 里的行号换算成文件里的行号
    assert vue["Cart.template"].kind == "template" and vue["Cart.template"].calls == ["CartList"]
    assert "购物车页面" in vue["Cart.template"].doc


def test_minified_and_unknown_files_are_skipped() -> None:
    assert parse_file("a.min.js", ("x=1;" * 5000).encode()) == []
    assert parse_file("README.md", b"# hi") == []


# ---------- 堆栈 ----------


def test_stack_formats() -> None:
    py = parse_frames('Traceback (most recent call last):\n  File "C:\\\\p\\\\a.py", line 3, in main\n'
                      '  File "C:\\\\p\\\\b.py", line 9, in helper\nValueError: x')
    assert [(f.file[-4:], f.line, f.func) for f in py] == [("b.py", 9, "helper"), ("a.py", 3, "main")]  # 出错的地方在前

    node = parse_frames("TypeError: x\n    at Cart.total (http://localhost:5173/src/cart.ts?t=123:40:7)\n    at main (C:\\p\\main.js:3:1)")
    assert [(f.file, f.line, f.func) for f in node] == [("src/cart.ts", 40, "Cart.total"), ("C:\\p\\main.js", 3, "main")]

    java = parse_frames("java.lang.NullPointerException\n\tat com.shop.cart.CheckoutService.checkout(CheckoutService.java:9)")
    assert java[0].java_class == "com.shop.cart.CheckoutService" and java[0].func == "checkout"

    tsc = parse_frames("src/a.ts(3,7): error TS2322: Type 'string'\ntests/test_x.py:12: in test_y")
    assert [(f.file, f.line) for f in tsc] == [("src/a.ts", 3), ("tests/test_x.py", 12)]


# ---------- 索引 ----------


def test_incremental_refresh(tmp_path: Path) -> None:
    root = make_project(tmp_path / "proj")
    idx = CodeIndex(root, tmp_path / "code.sqlite3", lambda: None)
    first = idx.refresh()
    assert first.files == 5 and first.parsed == 5  # node_modules 里的不算

    assert idx.refresh().parsed == 0  # 没改：一个都不用重新解析

    cart = root / "shop/cart.py"
    cart.write_text(PY.replace("def price_of(item):", "def price_of(item, default=0):"), encoding="utf-8")
    again = idx.refresh()
    assert again.parsed == 1
    assert idx.symbol("price_of")[0].signature == "def price_of(item, default=0)"

    time.sleep(0.01)
    cart.write_text(cart.read_text(encoding="utf-8"), encoding="utf-8")  # 只是保存了一下
    assert idx.refresh().parsed == 0

    (root / "web/src/CartView.ts").unlink()
    gone = idx.refresh()
    assert gone.removed == 1 and not idx.symbol("CartView")


def test_structure_first_vectors_later(tmp_path: Path) -> None:
    """只更新结构时向量留空（星图、定位不用等），之后补上，和一次算完的一样"""
    root = make_project(tmp_path / "proj")
    quick = CodeIndex(root, tmp_path / "a.sqlite3", lambda: FakeEmbedder())  # type: ignore[arg-type,return-value]
    stats = quick.refresh(embed=False)
    assert stats.embedded == 0 and stats.chunks > 0
    assert quick.locate('File "x/shop/cart.py", line 7, in price_of')[0].chunk is not None  # 不用向量也能定位
    assert quick.embed_missing() == stats.chunks
    assert quick.embed_missing() == 0

    full = CodeIndex(root, tmp_path / "b.sqlite3", lambda: FakeEmbedder())  # type: ignore[arg-type,return-value]
    full.refresh()
    assert [h.qname for h in quick.search("购物车里的东西")] == [h.qname for h in full.search("购物车里的东西")]


def test_search_by_name_keyword_and_vector(index: CodeIndex) -> None:
    assert index.search("Cart.total")[0].qname == "Cart.total"
    # 同名的都排在最前：checkout.py 的顶层代码块也叫 checkout，所以看前三条
    top = index.search("checkout")[:3]
    assert any(h.qname == "checkout" and h.kind == "function" for h in top)
    assert "CheckoutService.checkout" in [h.qname for h in top]
    assert any(h.qname == "price_of" for h in index.search("查单价"))  # 中文注释也能搜
    hits = index.search("购物车里的东西")
    assert "Cart" in [h.qname for h in hits]
    # 命中文件或类时，往里面挑相关的函数（排在后面）
    assert any(h.kind in ("function", "method") for h in hits)


def test_drill_down_survives_tied_scores(tmp_path: Path) -> None:
    """往文件里细找时两个函数分数一样（比如内容相同），不能因为比较不了块而出错"""

    class Flat(FakeEmbedder):
        def _vec(self, text: str) -> np.ndarray:
            v = np.ones(self.dim, np.float32)
            return v / np.linalg.norm(v)

    root = tmp_path / "p"
    root.mkdir()
    (root / "twins.py").write_text('"""双胞胎"""\n\ndef a():\n    return 1\n\n\ndef b():\n    return 1\n', encoding="utf-8")
    idx = CodeIndex(root, tmp_path / "t.sqlite3", lambda: Flat())  # type: ignore[arg-type,return-value]
    idx.refresh()
    assert {h.qname for h in idx.search("双胞胎", k=6)} >= {"a", "b"}


def test_locate_and_call_graph(index: CodeIndex) -> None:
    tb = (f'Traceback (most recent call last):\n  File "{index.root / "shop" / "cart.py"}", line 19, in total\n'
          "TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'")
    loc = index.locate(tb)[0]
    assert loc.file == "shop/cart.py" and loc.chunk is not None and loc.chunk.qname == "Cart.total"
    assert [c.qname for c in index.callees(loc.chunk)][:2] == ["price_of", "sum"] or "price_of" in [c.qname for c in index.callees(loc.chunk)]
    assert "Cart.total" in [c.qname for c in index.callers(index.symbol("price_of")[0])]

    # 另一台电脑上的路径、只有相对路径：按结尾对上的路径段找
    assert index.locate('File "/home/ci/build/shop/cart.py", line 7, in price_of')[0].chunk.qname == "price_of"  # type: ignore[union-attr]
    # Java 按包名拼路径
    java = index.locate("\tat com.shop.cart.CheckoutService.checkout(CheckoutService.java:9)")[0]
    assert java.chunk is not None and java.chunk.qname == "CheckoutService.checkout"
    # 第三方库里的帧：不在项目里
    lib = index.locate('File "C:\\Python312\\Lib\\json\\decoder.py", line 3, in decode')[0]
    assert lib.chunk is None


# ---------- 星图 ----------


def test_graph_links_files_by_calls(index: CodeIndex) -> None:
    g = index.graph({"shop/cart.py"})
    nodes = {n["id"]: n for n in g["nodes"]}
    assert set(nodes) == {"shop/cart.py", "shop/checkout.py", "web/src/CartView.ts", "web/src/Cart.vue",
                          "server/src/main/java/com/shop/cart/CheckoutService.java"}
    assert nodes["shop/cart.py"]["modified"] and not nodes["shop/checkout.py"]["modified"]
    assert nodes["shop/cart.py"]["functions"] == 5 and nodes["shop/cart.py"]["group"] == "shop"
    links = {(lk["source"], lk["target"]): lk["weight"] for lk in g["links"] if lk["kind"] == "call"}
    # checkout 调了 Cart()、price_of()、cart.total()：TS 里也有个 total，但只在同一类语言里连，Python 里只有一个，连得上；
    # Python 的 Cart() 也不会连到 Vue 的 Cart 组件
    assert links == {("shop/checkout.py", "shop/cart.py"): 3}


def test_error_chain_goes_up_the_callers(index: CodeIndex) -> None:
    price_of = index.symbol("price_of")[0]
    assert {c.qname for c in index.chain(price_of)} == {"Cart.total", "checkout"}
    assert [c.qname for c in index.outline("shop/cart.py")] == ["price_of", "Cart", "Cart.__init__", "Cart.total",
                                                                "Cart.empty", "Cart.empty.inner"]


def test_modified_files_from_git(tmp_path: Path) -> None:
    root = make_project(tmp_path / "proj")
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
    (root / "shop" / "cart.py").write_text(PY + "\n# 改了\n", encoding="utf-8")
    (root / "shop" / "new.py").write_text("x = 1\n", encoding="utf-8")
    projects = CodeProjects(tmp_path / "data", lambda: None)
    p = projects.add(str(root))
    assert projects.modified_files(p.id) == {"shop/cart.py", "shop/new.py"}


def test_star_map_api(tmp_path: Path) -> None:
    root = make_project(tmp_path / "proj")
    client = TestClient(app)
    pid = client.post("/code/projects", json={"path": str(root)}).json()["id"]
    for _ in range(100):
        if client.get("/code/projects").json()[0]["state"] == "ready":
            break
        time.sleep(0.05)

    g = client.get(f"/code/projects/{pid}/graph").json()
    assert len(g["nodes"]) == 5 and g["links"][0]["weight"] == 3
    outline = client.get(f"/code/projects/{pid}/outline", params={"file": "shop/cart.py"}).json()
    assert outline[0]["qname"] == "price_of" and outline[0]["doc"] == "查单价"
    chunk = client.get(f"/code/projects/{pid}/chunk", params={"file": "shop/cart.py", "line": 19}).json()
    assert chunk["qname"] == "Cart.total" and chunk["lines"][0].strip() == "def total(self):"

    located = client.post("/code/locate", json={"text": f'File "{root / "shop" / "cart.py"}", line 8, in price_of'}).json()
    assert located["project"] == pid and located["frames"][0]["qname"] == "price_of"
    assert {c["qname"] for c in located["chain"]} == {"Cart.total", "checkout"}
    assert client.post("/code/locate", json={"text": 'File "C:\\\\lib\\\\x.py", line 1, in f'}).json() is None


# ---------- 工具 ----------


@pytest.mark.anyio
async def test_code_tools(tmp_path: Path) -> None:
    projects = CodeProjects(tmp_path / "data", lambda: None)
    skill = code_skill(projects)
    assert not skill.available()  # 还没登记项目：不给她这些工具
    root = make_project(tmp_path / "proj")
    projects.add(str(root))
    assert skill.available()
    tools = {t.name: t for t in skill.tools}

    out = await tools["code_locate"].run({"error": f'File "{root / "shop" / "cart.py"}", line 19, in total\nTypeError: x'})
    assert "第 1 处：shop/cart.py:19，在 方法 Cart.total 里" in out
    assert "→ 19 |" in out and "return sum(price_of(i)" in out  # 出错的那一行标出来
    assert "它调用的：price_of" in out

    out = await tools["code_search"].run({"query": "查单价"})
    assert "shop/cart.py:6-8" in out

    out = await tools["code_read"].run({"target": "CheckoutService.checkout"})
    assert "public long checkout(long userId)" in out
    out = await tools["code_read"].run({"target": "shop/cart.py:15"})
    assert "def __init__(self)" in out

    out = await tools["code_locate"].run({"error": 'File "C:\\Python312\\Lib\\json\\decoder.py", line 3, in decode'})
    assert "不在登记的项目里" in out


# ---------- 接口 ----------


def test_project_api(tmp_path: Path) -> None:
    root = make_project(tmp_path / "proj")
    client = TestClient(app)
    assert client.post("/code/projects", json={"path": str(tmp_path / "nope")}).status_code == 422

    project = client.post("/code/projects", json={"path": str(root)}).json()
    for _ in range(100):
        listed = client.get("/code/projects").json()
        if listed and listed[0]["state"] == "ready":
            break
        time.sleep(0.05)
    assert listed[0]["files"] == 5 and listed[0]["name"] == "proj"

    hits = client.post(f"/code/projects/{project['id']}/search", json={"query": "Cart.total"}).json()
    assert hits[0]["qname"] == "Cart.total"
    located = client.post(f"/code/projects/{project['id']}/locate",
                          json={"text": "\tat com.shop.cart.CheckoutService.checkout(CheckoutService.java:9)"}).json()
    assert located[0]["chunk"] == "CheckoutService.checkout"

    # 登记后「代码库」技能包就出现了
    assert any(s["id"] == "code" and s["available"] for s in client.get("/skills").json())

    assert client.delete(f"/code/projects/{project['id']}").json() == {"ok": True}
    assert client.get("/code/projects").json() == []
    assert root.exists()  # 项目里的代码不动


def test_mock_model_locates_screen_errors(tmp_path: Path) -> None:
    """看屏幕看到报错、用户点了「好」：假模型也会先去找出错的代码"""
    from app.llm import MockModel

    root = make_project(tmp_path / "proj")
    deps.get_code_projects().add(str(root))
    app.dependency_overrides[deps.get_model] = lambda: MockModel(delay=0)
    try:
        msg = f'我这边报错了，帮我看看：\nFile "{root / "shop" / "cart.py"}", line 19, in total\nTypeError: x'
        res = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": msg}]})
    finally:
        app.dependency_overrides.clear()
    events = parse_sse(res.text)
    said = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert {"type": "status", "text": "正在找出错的代码"} in events
    assert "Cart.total" in said
