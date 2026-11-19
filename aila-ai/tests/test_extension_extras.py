"""扩展包第二、三期：动作、台词、形象、页面、对话里的扩展动作、扩展目录（本机目录和 https 目录）"""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest

from app.extension_catalog import browse, fetch_and_stage, load_sources, save_sources
from app.extension_extras import ExtrasError, motions_prompt, parse_lines, parse_motion
from app.extensions import ExtensionError, ExtensionStore
from app.runtime.runner import Runtime
from app.tags import TagParser

from .test_runtime import SHELL, Scripted

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "extensions"


def install(store: ExtensionStore, folder: Path) -> None:
    store.install(store.stage(folder)[0])


# ---------- 动作、台词 ----------


def test_motion_checks_and_clamps() -> None:
    m = parse_motion("spin", {"label": "转圈", "phases": [
        {"dur": 0.3, "pose": {"headYaw": 5, "armRaise": -1}, "expr": {"happy": 2}},
        {"dur": 0.2},
    ]})
    # 超出范围的值截到边上：不会把她扭成奇怪的样子
    assert m.phases[0] == {"dur": 0.3, "pose": {"headYaw": 0.8, "armRaise": 0.0}, "expr": {"happy": 1.0}}
    assert m.phases[1] == {"dur": 0.2} and m.label == "转圈"
    for name, raw, why in [
        ("nod", {"phases": [{"dur": 1}]}, "重名"),
        ("Spin", {"phases": [{"dur": 1}]}, "小写"),
        ("spin", {"phases": []}, "phases"),
        ("spin", {"phases": [{"dur": 9}]}, "dur"),
        ("spin", {"phases": [{"dur": 1, "pose": {"leftUpperArm": 1}}]}, "不认识"),
        ("spin", {"phases": [{"dur": 1, "expr": {"happy": "很"}}]}, "数字"),
        ("spin", {"phases": [{"dur": 4}] * 3}, "太长"),
    ]:
        with pytest.raises(ExtrasError, match=why):
            parse_motion(name, raw)


def test_lines_checks() -> None:
    lines, replace = parse_lines({"lines": {"pokeHead": ["  好痛 ", "", "x" * 200], "wake": []}})
    assert lines == {"pokeHead": ["好痛", "x" * 80]} and not replace
    assert parse_lines({"replace": True, "lines": {"wake": ["嗯"]}})[1]
    with pytest.raises(ExtrasError, match="不认识"):
        parse_lines({"lines": {"chatError": ["x"]}})
    with pytest.raises(ExtrasError, match="lines"):
        parse_lines({"pokeHead": ["x"]})


def test_plan_lists_extras_and_rejects_broken_motion(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / "exts")
    plan = store.stage(EXAMPLES / "cheer")[1]
    assert [m["name"] for m in plan["motions"]] == ["cheer", "shy"]
    assert plan["lines"] == {"pokeHead": 2, "testsRecovered": 1, "wake": 1} and not plan["linesReplace"]
    assert plan["pages"] == [{"name": "pomodoro", "title": "番茄钟"}]
    assert any("沙盒窗口" in w for w in plan["warnings"])

    broken = tmp_path / "broken"
    shutil.copytree(EXAMPLES / "cheer", broken)
    (broken / "motions" / "bad.json").write_text('{"phases": [{"dur": 99}]}', encoding="utf-8")
    with pytest.raises(ExtensionError, match="bad"):
        store.stage(broken)


def test_companion_merges_enabled_extensions(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / "exts")
    install(store, EXAMPLES / "cheer")
    other = tmp_path / "src" / "quiet"
    (other / "avatars").mkdir(parents=True)
    (other / "avatars" / "小艾.vrm").write_bytes(b"glTF")
    (other / "motions").mkdir()
    (other / "motions" / "cheer.json").write_text('{"phases": [{"dur": 1}]}', encoding="utf-8")  # 和 cheer 重名：先装的算
    (other / "lines.json").write_text(json.dumps({"replace": True, "lines": {"wake": ["……早。"]}}, ensure_ascii=False), encoding="utf-8")
    (other / "aila-extension.json").write_text('{"id": "quiet", "version": "1.0"}', encoding="utf-8")
    install(store, other)

    c = store.companion()
    assert set(c["motions"]) == {"cheer", "shy"} and c["motions"]["cheer"]["label"].startswith("欢呼")
    assert c["lines"]["add"]["pokeHead"][0] == "……又、又戳？" and c["lines"]["replace"] == {"wake": ["……早。"]}
    assert c["avatars"][0]["name"] == "小艾" and c["avatars"][0]["extension"] == "quiet"
    assert c["avatars"][0]["kind"] == "vrm"
    assert store.avatar_file("quiet", "小艾").name == "小艾.vrm"
    with pytest.raises(ExtensionError):
        store.avatar_file("quiet", "../../x")

    store.set_enabled("cheer", False)
    c = store.companion()
    assert set(c["motions"]) == {"cheer"} and "pokeHead" not in c["lines"]["add"]  # 停用的扩展不给
    with pytest.raises(ExtensionError, match="停用"):
        store.page("cheer", "pomodoro")


# ---------- 对话里用扩展的动作 ----------


def test_chat_knows_extension_motions() -> None:
    plain = TagParser()
    assert [e for e in plain.feed("[cheer]好耶。") if e["type"] == "action"] == []
    parser = TagParser(frozenset({"cheer"}))
    events = parser.feed("[cheer]好耶。")
    assert {"type": "action", "name": "cheer"} in events
    assert not any("[cheer]" in e.get("text", "") for e in events)  # 标签不念出来
    m = parse_motion("cheer", {"label": "欢呼", "phases": [{"dur": 1}]})
    assert "[cheer] 欢呼" in motions_prompt([m]) and motions_prompt([]) == ""


@pytest.mark.anyio
async def test_http_companion_and_page(tmp_path: Path) -> None:
    from app.deps import get_runtime
    from app.main import app

    rt = Runtime(tmp_path / "data", lambda: Scripted([["好"]]), shell=SHELL)
    install(rt.extensions, EXAMPLES / "cheer")
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            comp = (await c.get("/extensions/companion")).json()
            assert set(comp["motions"]) == {"cheer", "shy"}
            page = await c.get("/extensions/cheer/pages/pomodoro")
            assert page.status_code == 200 and "番茄钟" in page.text
            assert "sandbox" in page.headers["content-security-policy"] and "connect-src 'none'" in page.headers["content-security-policy"]
            assert (await c.get("/extensions/cheer/pages/..%2F..%2Faila-extension")).status_code == 404
            listed = (await c.get("/extensions")).json()["extensions"][0]
            assert listed["pages"] == [{"name": "pomodoro", "title": "番茄钟"}] and len(listed["motions"]) == 2
    finally:
        app.dependency_overrides.pop(get_runtime, None)
        await rt.shutdown()


# ---------- 目录 ----------


def make_catalog(folder: Path, tamper: bool = False, wrong_id: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    src = EXAMPLES / "git-helper"
    zpath = folder / "git-helper-1.0.0.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix())
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    entry = {"id": "other" if wrong_id else "git-helper", "name": "Git 小助手", "version": "1.0.0",
             "url": zpath.name, "sha256": "0" * 64 if tamper else digest}
    cat = folder / "catalog.json"
    cat.write_text(json.dumps({"name": "测试目录", "extensions": [entry, {"id": "坏的"}]}, ensure_ascii=False), encoding="utf-8")
    return cat


@pytest.mark.anyio
async def test_local_catalog_browse_and_install(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / "exts")
    cat = make_catalog(tmp_path / "cat")
    with pytest.raises(ExtensionError, match="https"):
        save_sources(store, ["http://example.com/catalog.json"])
    with pytest.raises(ExtensionError, match="完整路径"):
        save_sources(store, ["catalog.json"])
    assert save_sources(store, [str(cat), str(cat)]) == [str(cat)] and load_sources(store) == [str(cat)]

    async with httpx.AsyncClient() as client:
        [listing] = await browse(store, client)
        assert listing["name"] == "测试目录" and listing["error"] == ""
        assert [e["id"] for e in listing["extensions"]] == ["git-helper"]  # 写错的条目跳过
        assert listing["extensions"][0]["installed"] is None

        token, plan = await fetch_and_stage(store, str(cat), "git-helper", client)
        assert plan["id"] == "git-helper" and plan["commands"] and plan["source"] == str(cat)
        store.install(token)
        [listing] = await browse(store, client)
        assert listing["extensions"][0]["installed"] == "1.0.0" and not listing["extensions"][0]["upgrade"]

        with pytest.raises(ExtensionError, match="没有这个目录"):
            await fetch_and_stage(store, str(tmp_path / "x.json"), "git-helper", client)

        bad = make_catalog(tmp_path / "bad", tamper=True)
        save_sources(store, [str(bad)])
        with pytest.raises(ExtensionError, match="sha256"):
            await fetch_and_stage(store, str(bad), "git-helper", client)

        wrong = make_catalog(tmp_path / "wrong", wrong_id=True)
        save_sources(store, [str(wrong)])
        with pytest.raises(ExtensionError, match="不是目录里写的"):
            await fetch_and_stage(store, str(wrong), "other", client)

        escape = tmp_path / "esc"
        escape.mkdir()
        (escape / "catalog.json").write_text(json.dumps({"extensions": [
            {"id": "git-helper", "version": "1.0.0", "url": "../cat/git-helper-1.0.0.zip", "sha256": "0" * 64}]}), encoding="utf-8")
        save_sources(store, [str(escape / "catalog.json")])
        with pytest.raises(ExtensionError, match="文件夹里"):
            await fetch_and_stage(store, str(escape / "catalog.json"), "git-helper", client)
    assert not list((store.root / ".staging").iterdir())


@pytest.mark.anyio
async def test_remote_catalog_only_public_https(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / "exts")
    folder = tmp_path / "cat"
    make_catalog(folder)
    zip_bytes = (folder / "git-helper-1.0.0.zip").read_bytes()
    catalog = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    catalog["extensions"][0]["url"] = "files/git-helper.zip"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/catalog.json":
            return httpx.Response(200, json=catalog)
        if request.url.path == "/files/git-helper.zip":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/git-helper.zip"})
        if request.url.host == "cdn.example.com":
            return httpx.Response(200, content=zip_bytes)
        if request.url.path == "/evil.json":
            return httpx.Response(302, headers={"location": "https://internal.example/catalog.json"})
        return httpx.Response(404)

    async def resolve(host: str) -> list[str]:
        return ["10.0.0.5"] if host == "internal.example" else ["93.184.216.34"]

    save_sources(store, ["https://exts.example.com/catalog.json", "https://exts.example.com/evil.json"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        good, evil = await browse(store, client, resolve)
        assert [e["id"] for e in good["extensions"]] == ["git-helper"]
        assert "内网" in evil["error"]  # 跳到内网地址：拦下
        token, plan = await fetch_and_stage(store, "https://exts.example.com/catalog.json", "git-helper", client, resolve)
        assert plan["id"] == "git-helper"
        assert "https://cdn.example.com/git-helper.zip" in seen  # 跟着跳转、每一跳都查过
        store.discard(token)


def test_live2d_avatars_are_whole_folders(tmp_path: Path) -> None:
    from app.extension_extras import scan_avatars

    root = tmp_path / "ext"
    l2d = root / "avatars" / "hiyori"
    (l2d / "textures").mkdir(parents=True)
    (l2d / "hiyori.model3.json").write_text("{}", encoding="utf-8")
    (l2d / "hiyori.moc3").write_bytes(b"MOC3")
    (l2d / "textures" / "t.png").write_bytes(b"png")
    (root / "avatars" / "two").mkdir()  # 两个 model3.json：认不出是哪个，不算
    (root / "avatars" / "two" / "a.model3.json").write_text("{}", encoding="utf-8")
    (root / "avatars" / "two" / "b.model3.json").write_text("{}", encoding="utf-8")
    (root / "avatars" / "empty").mkdir()
    (root / "avatars" / "v.vrm").write_bytes(b"glTF")
    found = {a["name"]: a for a in scan_avatars(root)}
    assert set(found) == {"hiyori", "v"}
    assert found["hiyori"]["kind"] == "live2d" and found["hiyori"]["file"].endswith("hiyori.model3.json")
    assert found["hiyori"]["size"] == 2 + 4 + 3
    assert found["v"]["kind"] == "vrm"
