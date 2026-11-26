"""邮件：用 IMAP 读你的收件箱（QQ 邮箱、163、126、foxmail……），只读。

- 用邮箱里生成的「授权码」登录，不是登录密码；邮箱地址和授权码存在 .env（AILA_MAIL_USER / AILA_MAIL_PASS），不回显
- 只读：收件箱用只读方式打开（EXAMINE），取信用 BODY.PEEK——不会把信标成已读，也不删、不移、不发
- 服务器按邮箱后缀认（qq.com → imap.qq.com……），认不出的可以在 AILA_MAIL_HOST 里写
- 163、126 这几家要先报一下客户端身份（IMAP ID），不然会说「Unsafe Login」
- 每封最多取 64KB、正文最多给她 1500 字；信的内容只当数据（里面要求她做事的话不理会）
- 网络调用是阻塞的（imaplib），在线程里跑
"""

import email
import html
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

HOSTS = {
    "qq.com": "imap.qq.com", "foxmail.com": "imap.qq.com", "vip.qq.com": "imap.qq.com",
    "163.com": "imap.163.com", "126.com": "imap.126.com", "yeah.net": "imap.yeah.net", "vip.163.com": "imap.vip.163.com",
    "sina.com": "imap.sina.com", "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "gmail.com": "imap.gmail.com",
}
NEEDS_ID = ("163.com", "126.com", "yeah.net")
FETCH_BYTES = 65536
BODY_CHARS = 1500
MAX_COUNT = 30
TIMEOUT = 20


class MailError(Exception):
    """说给用户听的错误"""


@dataclass(frozen=True)
class Mail:
    sender: str
    subject: str
    date: str
    unread: bool
    body: str

    def public(self) -> dict[str, Any]:
        return {"from": self.sender, "subject": self.subject, "date": self.date, "unread": self.unread}


def host_for(user: str, override: str = "") -> str:
    if override.strip():
        return override.strip()
    domain = user.rsplit("@", 1)[-1].lower()
    if domain in HOSTS:
        return HOSTS[domain]
    raise MailError(f"认不出 {domain} 的收信服务器：在设置里写上 IMAP 服务器地址。")


def _header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def _text(msg: Message) -> str:
    """正文：先找纯文本，没有就把网页版去掉标签"""
    plain, rich = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except (LookupError, AssertionError):
            continue
        if part.get_content_subtype() == "plain" and not plain:
            plain = text
        elif part.get_content_subtype() == "html" and not rich:
            rich = text
    if not plain and rich:
        rich = re.sub(r"(?is)<(script|style).*?</\1>", " ", rich)
        plain = html.unescape(re.sub(r"<[^>]+>", " ", rich))
    plain = re.sub(r"[ \t\r\f\v]+", " ", plain)
    plain = re.sub(r"\n\s*\n+", "\n", plain).strip()
    return plain[:BODY_CHARS]


def parse_mail(raw: bytes, flags: bytes) -> Mail:
    msg = email.message_from_bytes(raw)
    when = ""
    try:
        when = parsedate_to_datetime(msg.get("Date", "")).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError, IndexError):
        pass
    return Mail(_header(msg.get("From")), _header(msg.get("Subject")) or "（没有主题）", when,
                b"\\Seen" not in flags, _text(msg))


def _flags(meta: bytes) -> bytes:
    m = re.search(rb"FLAGS \(([^)]*)\)", meta)
    return m.group(1) if m else b""


def recent(user: str, password: str, host: str = "", count: int = 10, unread_only: bool = False, days: int = 7,
           imap: Any = None) -> list[Mail]:
    """收件箱里最近几封（新的在前）。imap 测试时换成假的"""
    count = max(1, min(MAX_COUNT, count))
    server = host_for(user, host)
    try:
        conn = imap or imaplib.IMAP4_SSL(server, 993, timeout=TIMEOUT)
    except OSError as err:
        raise MailError(f"连不上 {server}：{type(err).__name__}") from err
    try:
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as err:
            raise MailError("登录不上：看看邮箱地址和授权码对不对，邮箱设置里 IMAP 开了没（授权码不是登录密码）。") from err
        if user.rsplit("@", 1)[-1].lower() in NEEDS_ID:
            imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))
            conn._simple_command("ID", '("name" "aila" "version" "1.0" "vendor" "aila")')
        typ, _ = conn.select("INBOX", readonly=True)  # 只读打开：不改任何标记
        if typ != "OK":
            raise MailError("收件箱打不开。")
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        criteria = ["UNSEEN", "SINCE", since] if unread_only else ["SINCE", since]
        typ, data = conn.search(None, *criteria)
        ids = (data[0] or b"").split()[-count:] if typ == "OK" and data else []
        mails = []
        for mid in reversed(ids):
            typ, parts = conn.fetch(mid, f"(FLAGS BODY.PEEK[]<0.{FETCH_BYTES}>)")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            meta, raw = parts[0]
            # FLAGS 有的服务器放在正文前面，有的放在后面
            tail = b"".join(p for p in parts[1:] if isinstance(p, bytes))
            mails.append(parse_mail(raw, _flags(meta + tail)))
        return mails
    finally:
        try:
            conn.logout()
        except Exception:
            pass
