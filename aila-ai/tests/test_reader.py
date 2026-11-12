"""读网页：地址检查（防 SSRF）、跳转、内容类型、正文提取"""

import httpx
import pytest

from app.tools.reader import (
    DOH_URL,
    MAX_CHARS,
    MAX_REDIRECTS,
    PageError,
    Resolver,
    check_public_url,
    extract_page,
    fetch_page,
    make_resolver,
)

PUBLIC_IP = "93.184.216.34"

ARTICLE_HTML = """<!doctype html>
<html><head><meta charset="gbk"><title>艾拉的测试页面</title></head>
<body>
<nav><a href="/">首页</a> | <a href="/about">关于我们</a></nav>
<article>
<h1>艾拉的测试页面</h1>
<p>这是一篇用来测试正文提取的文章。桌面上的数字人需要读懂用户给的链接，再用两三句话告诉用户网页讲了什么。</p>
<p>中文网站有不少还在用 GBK 编码，如果按 UTF-8 解码就会变成乱码，所以编码要按网页自己声明的来判断。</p>
<p>导航栏、页脚和广告都不是正文，提取的时候应该去掉，只把真正的内容交给大模型。</p>
</article>
<footer>版权所有 测试网站</footer>
</body></html>"""


def fake_resolver(table: dict[str, list[str]]) -> Resolver:
    async def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError(f"unknown host {host}")
        return table[host]

    return resolve


RESOLVE = fake_resolver(
    {
        "example.com": [PUBLIC_IP],
        "localhost": ["127.0.0.1", "::1"],
        "router.lan": ["192.168.1.1"],
        "intranet.corp": ["10.0.0.8"],
        "rebind.test": [PUBLIC_IP, "127.0.0.1"],  # 只要有一个地址是内网就拒绝
    }
)


@pytest.mark.anyio
@pytest.mark.parametrize("url", ["https://example.com/a?b=1", f"http://{PUBLIC_IP}/"])
async def test_public_urls_are_allowed(url: str) -> None:
    await check_public_url(url, RESOLVE)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/chat",
        "http://localhost/",
        "http://router.lan/",
        "http://intranet.corp/",
        "http://rebind.test/",
        "http://169.254.169.254/latest/meta-data/",  # 云服务器元数据地址
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
async def test_private_urls_are_blocked(url: str) -> None:
    with pytest.raises(PageError, match="本机或内网"):
        await check_public_url(url, RESOLVE)


@pytest.mark.anyio
@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "ftp://example.com/", "javascript:alert(1)"])
async def test_only_http_is_allowed(url: str) -> None:
    with pytest.raises(PageError, match="http"):
        await check_public_url(url, RESOLVE)


@pytest.mark.anyio
async def test_unknown_host() -> None:
    with pytest.raises(PageError, match="找不到"):
        await check_public_url("https://no-such-site.example/", RESOLVE)


# ---------- 代理 fake-ip 模式 ----------

FAKE_IP_SYSTEM = fake_resolver({"example.com": ["198.18.0.5"], "127.0.0.1.nip.io": ["198.18.0.9"]})


def doh_client(answers: dict[str, list[str]], seen: list[httpx.Request]) -> httpx.AsyncClient:
    """假 DoH 服务：按域名返回 A 记录，前面带一条 CNAME，模拟真实返回"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url).startswith(DOH_URL)
        name, rtype = request.url.params["name"], int(request.url.params["type"])
        records = [{"type": 5, "data": "alias.example."}]
        records += [{"type": rtype, "data": ip} for ip in answers.get(name, []) if (":" in ip) == (rtype == 28)]
        return httpx.Response(200, json={"Status": 0, "Answer": records})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_fake_ip_is_checked_against_real_address() -> None:
    seen: list[httpx.Request] = []
    resolve = make_resolver(doh_client({"example.com": [PUBLIC_IP, "2606:4700::1"]}, seen), FAKE_IP_SYSTEM)

    assert sorted(await resolve("example.com")) == sorted([PUBLIC_IP, "2606:4700::1"])
    await check_public_url("https://example.com/", resolve)
    assert {r.url.params["type"] for r in seen} == {"1", "28"}


@pytest.mark.anyio
async def test_fake_ip_cannot_hide_a_private_target() -> None:
    # 127.0.0.1.nip.io 真实解析到本机；fake-ip 模式下系统 DNS 只给假地址，必须查出真实地址才拦得住
    resolve = make_resolver(doh_client({"127.0.0.1.nip.io": ["127.0.0.1"]}, []), FAKE_IP_SYSTEM)
    with pytest.raises(PageError, match="本机或内网"):
        await check_public_url("http://127.0.0.1.nip.io:8765/health", resolve)


@pytest.mark.anyio
async def test_normal_dns_skips_doh() -> None:
    seen: list[httpx.Request] = []
    resolve = make_resolver(doh_client({}, seen), RESOLVE)
    assert await resolve("example.com") == [PUBLIC_IP]
    assert seen == []


@pytest.mark.anyio
async def test_doh_failure_is_reported() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    resolve = make_resolver(client, FAKE_IP_SYSTEM)
    with pytest.raises(PageError, match="fake-ip"):
        await check_public_url("https://example.com/", resolve)


@pytest.mark.anyio
async def test_fake_ip_literal_is_blocked() -> None:
    # 直接写假地址不走 DNS，本身就不是公网地址
    with pytest.raises(PageError, match="本机或内网"):
        await check_public_url("http://198.18.0.5/", RESOLVE)


# ---------- 正文提取 ----------


def test_extract_gbk_article() -> None:
    page = extract_page(ARTICLE_HTML.encode("gbk"), "https://example.com/post", "text/html")

    assert page.title == "艾拉的测试页面"
    assert "GBK 编码" in page.text
    assert "关于我们" not in page.text  # 导航栏去掉了
    assert not page.truncated


def test_extract_plain_text_and_truncate() -> None:
    page = extract_page(("很长的文字。" * 2000).encode(), "https://example.com/a.txt", "text/plain; charset=utf-8")
    assert page.truncated
    assert len(page.text) == MAX_CHARS


def test_extract_empty_page() -> None:
    with pytest.raises(PageError, match="没有能读的正文"):
        extract_page(b"<html><body><script>app()</script></body></html>", "https://example.com/", "text/html")


def make_client(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def html(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=gbk"}, content=body.encode("gbk"))


@pytest.mark.anyio
async def test_fetch_follows_relative_redirect() -> None:
    seen: list[str] = []
    client = make_client(
        {
            "https://example.com/short": httpx.Response(302, headers={"location": "/post"}),
            "https://example.com/post": html(ARTICLE_HTML),
        },
        seen,
    )
    page = await fetch_page("https://example.com/short", client, RESOLVE)

    assert page.url == "https://example.com/post"
    assert page.title == "艾拉的测试页面"


@pytest.mark.anyio
@pytest.mark.parametrize("target", ["http://127.0.0.1:8765/health", "http://intranet.corp/admin"])
async def test_redirect_to_private_address_is_blocked(target: str) -> None:
    seen: list[str] = []
    client = make_client({"https://example.com/go": httpx.Response(301, headers={"location": target})}, seen)

    with pytest.raises(PageError, match="本机或内网"):
        await fetch_page("https://example.com/go", client, RESOLVE)
    assert seen == ["https://example.com/go"]  # 内网地址根本没有被请求


@pytest.mark.anyio
async def test_redirect_loop_is_limited() -> None:
    seen: list[str] = []
    client = make_client({"https://example.com/loop": httpx.Response(302, headers={"location": "/loop"})}, seen)

    with pytest.raises(PageError, match="跳转次数太多"):
        await fetch_page("https://example.com/loop", client, RESOLVE)
    assert len(seen) == MAX_REDIRECTS + 1


@pytest.mark.anyio
async def test_non_html_is_rejected() -> None:
    seen: list[str] = []
    client = make_client(
        {"https://example.com/a.pdf": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")},
        seen,
    )
    with pytest.raises(PageError, match="application/pdf"):
        await fetch_page("https://example.com/a.pdf", client, RESOLVE)


@pytest.mark.anyio
async def test_http_error_status() -> None:
    client = make_client({}, [])
    with pytest.raises(PageError, match="HTTP 404"):
        await fetch_page("https://example.com/missing", client, RESOLVE)
