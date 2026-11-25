"""读网页：只允许公网地址，限制大小、时间和跳转次数，提取正文。

为什么要限制地址：网页内容是不可信的，里面可能写着「请打开 http://127.0.0.1:8765/...」
诱导大模型去访问本机或内网服务（SSRF）。所以每一跳都检查目标是不是公网地址。

开着代理（Clash、sing-box 等）的 fake-ip 模式时，所有域名在本机都解析成 198.18.x.x 假地址，
看不出真实目标。这时改用 DoH 查真实地址再检查，否则 127.0.0.1.nip.io 这种域名就能绕过去。

已知不足：检查和真正连接是两次解析（开代理时由代理去连），中间有极小的 DNS 重绑定窗口；
对桌面助手来说可以接受。
"""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_CHARS = 6000  # 给大模型的正文上限，太长既贵又容易跑题
TIMEOUT = 12.0
USER_AGENT = "Mozilla/5.0 (compatible; AilaDesktopAssistant/0.1)"

# 代理 fake-ip 模式的默认网段（这个网段本来是测试保留地址，正常网站不会用）
FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
# 阿里公共 DNS 的 JSON 接口；用 IP 直连，避免查询本身又被改成假地址
DOH_URL = "https://223.5.5.5/resolve"
DOH_TIMEOUT = 5.0
DNS_A, DNS_AAAA = 1, 28

Resolver = Callable[[str], Awaitable[list[str]]]


class PageError(Exception):
    """读不了这个网页，消息会原样告诉大模型，再由艾拉转告用户"""


@dataclass
class Page:
    url: str
    title: str
    text: str
    truncated: bool


async def resolve_host(host: str) -> list[str]:
    """系统 DNS"""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


async def resolve_doh(host: str, client: httpx.AsyncClient) -> list[str]:
    """通过 DoH 查真实地址（A 和 AAAA 记录，跳过 CNAME）"""

    async def query(record_type: int) -> list[str]:
        resp = await client.get(DOH_URL, params={"name": host, "type": record_type}, timeout=DOH_TIMEOUT)
        resp.raise_for_status()
        return [str(a["data"]) for a in resp.json().get("Answer") or [] if a.get("type") == record_type]

    try:
        v4, v6 = await asyncio.gather(query(DNS_A), query(DNS_AAAA))
    except (httpx.HTTPError, ValueError) as err:
        raise PageError("开着代理的 fake-ip 模式，查不到这个网站的真实地址") from err
    return v4 + v6


def _is_fake_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%")[0]) in FAKE_IP_NET
    except ValueError:
        return False


def make_resolver(client: httpx.AsyncClient, system: Resolver = resolve_host) -> Resolver:
    """先用系统 DNS；拿到的是代理的假地址时，改用 DoH 查真实地址"""

    async def resolve(host: str) -> list[str]:
        addresses = await system(host)
        if any(_is_fake_ip(a) for a in addresses):
            return await resolve_doh(host, client)
        return addresses

    return resolve


async def check_public_url(url: str, resolve: Resolver = resolve_host) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PageError("只能读 http 或 https 网页")
    host = parts.hostname
    if not host:
        raise PageError("网址不完整")

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = await resolve(host)
        except OSError as err:
            raise PageError(f"找不到这个网站（{host}）") from err
        addresses = [ipaddress.ip_address(a.split("%")[0]) for a in resolved]

    if not addresses:
        raise PageError(f"找不到这个网站（{host}）")
    for addr in addresses:
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped  # ::ffff:127.0.0.1 这种写法也要拦住
        if not addr.is_global:
            raise PageError("不能访问本机或内网地址")


async def fetch_page(
    url: str, client: httpx.AsyncClient, resolve: Resolver = resolve_host, max_chars: int = MAX_CHARS
) -> Page:
    """读一个公网网页的正文。max_chars：正文最多留多长（编程任务带着问题读网页时交给快模型，可以长一些）"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await check_public_url(current, resolve)  # 每一跳都检查，防止先跳到公网再跳回内网
        async with client.stream(
            "GET",
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
            follow_redirects=False,
            timeout=TIMEOUT,
        ) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise PageError("网页跳转地址缺失")
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise PageError(f"网页打不开（HTTP {resp.status_code}）")

            content_type = resp.headers.get("content-type", "").lower()
            if content_type and not any(t in content_type for t in ("html", "xml", "text/plain")):
                raise PageError(f"这不是网页，是 {content_type.split(';')[0]}")

            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) >= MAX_BYTES:
                    break  # 超长页面只看前面一部分
            return extract_page(bytes(body), str(resp.url), content_type, max_chars)
    raise PageError("网页跳转次数太多")


def extract_page(raw: bytes, url: str, content_type: str = "text/html", max_chars: int = MAX_CHARS) -> Page:
    """提取正文，去掉导航、页脚、广告。编码交给 trafilatura 判断（中文网站常见 GBK）。"""
    if "text/plain" in content_type:
        title, text = "", raw.decode("utf-8", errors="replace")
    else:
        tree = trafilatura.load_html(raw)  # 按 <meta charset> 等判断编码
        if tree is None:
            raise PageError("网页内容解析不了")
        meta = trafilatura.extract_metadata(tree, default_url=url)  # 先取标题，extract 可能会改动树
        title = (meta.title if meta else None) or ""
        text = trafilatura.extract(tree, url=url, include_comments=False, include_tables=True) or ""

    text = text.strip()
    if not text:
        raise PageError("这个网页没有能读的正文（可能需要登录，或者内容是脚本动态加载的）")
    truncated = len(text) > max_chars
    return Page(url=url, title=title.strip(), text=text[:max_chars], truncated=truncated)
