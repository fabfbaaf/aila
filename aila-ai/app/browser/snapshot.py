"""网页快照：她「看」网页的方式。

大模型（DeepSeek 等）只看得懂文字，所以每次操作后把页面整理成：标题、网址、可以操作的元素（带编号）、正文节选。
元素编号写在页面元素的 data-aila-ref 属性上，之后按编号操作——点的一定是她看到、用户确认过的那个元素，
页面重新渲染后编号消失，就会提示「页面变了」，而不会点到别的东西上。

每个元素记下判断风险要用的信息：是不是普通链接（点了只是打开网页）、在不在表单里、表单用 GET 还是 POST、
是不是密码 / 卡号 / 验证码这类敏感输入框。
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..tools import UNTRUSTED_NOTE

MAX_ELEMENTS = 80
MAX_TEXT = 3000

KIND_NAMES = {
    "link": "链接",
    "button": "按钮",
    "input": "输入框",
    "select": "下拉框",
    "checkbox": "勾选框",
    "file": "上传文件",
}

# 在页面里执行：标出可以操作的元素，返回它们的信息和正文。先挑视口里的，再按页面顺序编号
SNAPSHOT_JS = r"""
([maxElements, maxText]) => {
  const SEL = [
    'a[href]', 'button', 'input:not([type=hidden])', 'textarea', 'select', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=switch]', '[role=tab]',
    '[role=menuitem]', '[role=option]', '[role=combobox]', '[role=searchbox]', '[role=textbox]',
    '[contenteditable=""]', '[contenteditable=true]'
  ].join(',')
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim()
  for (const el of document.querySelectorAll('[data-aila-ref]')) el.removeAttribute('data-aila-ref')

  const vh = window.innerHeight
  const cands = []
  for (const el of document.querySelectorAll(SEL)) {
    if (cands.length >= 600) break
    if (el.disabled || el.closest('[aria-hidden="true"], [inert]')) continue
    const r = el.getBoundingClientRect()
    if (r.width < 2 || r.height < 2) continue
    const st = getComputedStyle(el)
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0) continue
    cands.push({ el, inView: r.bottom > 0 && r.top < vh })
  }
  const picked = new Set([...cands.filter((c) => c.inView), ...cands.filter((c) => !c.inView)].slice(0, maxElements).map((c) => c.el))

  const out = []
  for (const c of cands) {
    const el = c.el
    if (!picked.has(el)) continue
    const tag = el.tagName.toLowerCase()
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : '')).toLowerCase()
    const role = (el.getAttribute('role') || '').toLowerCase()
    const native = tag === 'input' && (type === 'checkbox' || type === 'radio')
    let kind = 'button'
    if (tag === 'a' || role === 'link') kind = 'link'
    else if (tag === 'select' || role === 'listbox') kind = 'select'
    else if (native || role === 'checkbox' || role === 'radio' || role === 'switch') kind = 'checkbox'
    else if (tag === 'input' && type === 'file') kind = 'file'
    else if ((tag === 'input' && !['submit', 'button', 'reset', 'image', 'range', 'color'].includes(type)) ||
             tag === 'textarea' || ['textbox', 'searchbox', 'combobox'].includes(role) || el.isContentEditable) kind = 'input'

    const label = clean(el.labels && el.labels[0] && el.labels[0].innerText)
    const img = el.querySelector && el.querySelector('img[alt]')
    const isField = kind === 'input' || kind === 'select' || kind === 'file'
    let name = clean(el.getAttribute('aria-label')) ||
      (isField ? label || clean(el.getAttribute('placeholder')) : clean(el.innerText)) ||
      label || clean(el.getAttribute('title')) || clean(img && img.alt) ||
      (tag === 'input' && ['submit', 'button', 'reset'].includes(type) ? clean(el.value) : '') ||
      clean(el.getAttribute('name'))

    const hint = [type, el.getAttribute('name'), el.id, el.getAttribute('autocomplete'),
                  el.getAttribute('placeholder'), label, el.getAttribute('aria-label')].join(' ').toLowerCase()
    const sensitive = kind === 'input' && (type === 'password' ||
      /cc-|card|cvv|cvc|one-time-code|otp|passw|pwd|密码|卡号|验证码|校验码|安全码|身份证|支付/.test(hint))

    const form = el.form || el.closest('form')
    const submits = !!form && ((tag === 'button' && (type === '' || type === 'submit')) ||
                               (tag === 'input' && (type === 'submit' || type === 'image')))
    const method = form ? ((submits && el.getAttribute('formmethod')) || form.getAttribute('method') || 'get').toLowerCase() : null
    const action = form ? ((submits && el.getAttribute('formaction') && el.formAction) || form.action || location.href) : null

    let value = null
    if (kind === 'input' && !sensitive) value = clean(el.isContentEditable ? el.innerText : el.value).slice(0, 60)
    if (kind === 'select' && tag === 'select') value = clean(el.selectedOptions[0] && el.selectedOptions[0].text)

    const ref = out.length + 1
    el.setAttribute('data-aila-ref', String(ref))
    out.push({
      ref, kind, name: name.slice(0, 60), tag, type, role,
      href: tag === 'a' ? el.href : null,
      download: tag === 'a' && el.hasAttribute('download'),
      value, sensitive, native,
      checked: native ? el.checked : el.getAttribute('aria-checked') === 'true',
      submits, method, action,
      search: type === 'search' || role === 'searchbox',
      inView: c.inView,
      options: tag === 'select' ? [...el.options].slice(0, 20).map((o) => clean(o.text)) : null
    })
  }

  const root = document.querySelector('main, article, [role=main]') || document.body
  const text = (root ? root.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim()
  return {
    url: location.href, title: document.title, elements: out,
    more: Math.max(0, cands.length - out.length),
    text: text.slice(0, maxText), textTruncated: text.length > maxText
  }
}
"""


@dataclass(frozen=True)
class Element:
    ref: int
    kind: str
    name: str
    tag: str
    type: str = ""
    role: str = ""
    href: str | None = None
    download: bool = False
    value: str | None = None
    sensitive: bool = False
    native: bool = False
    checked: bool = False
    submits: bool = False
    method: str | None = None  # 所在表单的提交方式；不在表单里为 None
    action: str | None = None
    search: bool = False
    in_view: bool = True
    options: tuple[str, ...] | None = None

    @property
    def label(self) -> str:
        return f"{KIND_NAMES.get(self.kind, '元素')}「{self.name or '（没有文字）'}」"

    @property
    def plain_link(self) -> bool:
        """普通链接：点它等于打开一个网页（GET），不用执行页面的脚本"""
        return (
            self.kind == "link"
            and bool(self.href)
            and urlsplit(self.href or "").scheme in ("http", "https")
            and not self.download
        )

    @property
    def get_form(self) -> bool:
        """在 GET 表单里：提交只是打开一个带参数的网址（比如搜索），没有副作用"""
        return self.method == "get"


@dataclass(frozen=True)
class Snapshot:
    url: str
    title: str
    elements: dict[int, Element]
    more: int
    text: str
    text_truncated: bool

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Snapshot":
        elements = {}
        for e in raw.get("elements") or []:
            el = Element(
                ref=int(e["ref"]), kind=str(e["kind"]), name=str(e.get("name") or ""), tag=str(e.get("tag") or ""),
                type=str(e.get("type") or ""), role=str(e.get("role") or ""), href=e.get("href"),
                download=bool(e.get("download")), value=e.get("value"), sensitive=bool(e.get("sensitive")),
                native=bool(e.get("native")), checked=bool(e.get("checked")), submits=bool(e.get("submits")),
                method=e.get("method"), action=e.get("action"), search=bool(e.get("search")),
                in_view=bool(e.get("inView", True)),
                options=tuple(e["options"]) if e.get("options") is not None else None,
            )
            elements[el.ref] = el
        return cls(str(raw.get("url") or ""), str(raw.get("title") or ""), elements, int(raw.get("more") or 0),
                   str(raw.get("text") or ""), bool(raw.get("textTruncated")))

    def render(self) -> str:
        """给大模型看的样子"""
        lines = [f"网页标题：{self.title or '（无）'}", f"网址：{self.url}", UNTRUSTED_NOTE, "", "可以操作的元素（用编号操作）："]
        for el in self.elements.values():
            lines.append(f"[{el.ref}] {el.label}{_extra(el)}")
        if not self.elements:
            lines.append("（没有）")
        if self.more:
            lines.append(f"（还有 {self.more} 个元素没列出来，可以往下翻页再看）")
        lines += ["", "正文节选：", self.text or "（没有文字）"]
        if self.text_truncated:
            lines.append("（正文太长，只给了前面一部分）")
        return "\n".join(lines)


def _extra(el: Element) -> str:
    notes = []
    if el.plain_link:
        notes.append(f"→ {short_url(el.href or '')}")
    if el.search:
        notes.append("搜索框")
    if el.sensitive:
        notes.append("密码 / 卡号 / 验证码之类，你不能填，要用户自己来")
    elif el.value:
        notes.append(f"现在是：{el.value}")
    if el.kind == "checkbox":
        notes.append("已勾选" if el.checked else "没勾选")
    if el.options:
        notes.append("选项：" + " / ".join(el.options))
    if el.submits:
        notes.append("提交表单")
    if not el.in_view:
        notes.append("在屏幕外")
    return "（" + "；".join(notes) + "）" if notes else ""


def short_url(url: str, limit: int = 80) -> str:
    parts = urlsplit(url)
    text = (parts.netloc + parts.path + (f"?{parts.query}" if parts.query else "")) or url
    return text if len(text) <= limit else text[:limit] + "…"
