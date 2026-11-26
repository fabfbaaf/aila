"""安卓屏幕上有什么（uiautomator 导出的界面树 → 带编号的元素清单），以及哪些操作要先问你。都是纯计算，能单测。

为什么用界面树不用截图：大模型（DeepSeek 这类）多数看不了图；界面树里有每个按钮、输入框的文字和位置，
她按编号点，坐标由代码从界面树算，不让大模型自己报坐标。

风险（和网页操作一个思路，见 app/browser/session.py）：
- 看屏幕、翻页、返回、回桌面、打开 App：直接做
- 点按钮：文字像「发送 / 支付 / 删除 / 确认 / 安装 / 允许……」的先问你；在支付、银行这类 App 里点什么都先问
- 打字：密码框、验证码、卡号这类不碰（让你自己填）
"""

import re
from dataclasses import dataclass
from xml.etree import ElementTree

# 点了会产生实际后果的按钮（发出去、付钱、删东西、装软件、给权限……）：先问你
RISKY = re.compile(
    r"发送|发出|发布|发表|提交|确认|确定|同意|允许|授权|支付|付款|转账|收款|购买|下单|结算|充值|提现|红包|"
    r"删除|清空|移除|卸载|安装|更新|注销|退出登录|登录|注册|绑定|解绑|拨打|呼叫|分享|转发|关注|订阅|保存|"
    r"send|post|publish|submit|confirm|agree|allow|grant|pay|buy|purchase|checkout|order|transfer|delete|remove|"
    r"uninstall|install|update|sign ?in|log ?in|sign ?up|register|call|share|follow|subscribe|save",
    re.IGNORECASE,
)
# 这些 App 里点什么都先问（钱、银行）
SENSITIVE_APPS = (
    "com.eg.android.AlipayGphone", "com.tencent.mm.plugin.wallet", "com.unionpay", "com.icbc", "com.chinamworld",
    "cmb.pb", "com.android.bankabc", "com.bankcomm", "com.ecitic", "com.cib", "com.spdbccc", "com.pingan",
    "com.jingdong.app.mall.jdpay", "com.paypal", "com.google.android.apps.walletnfcrel",
)
# 输入框像是密码、验证码、卡号：不填
SECRET_FIELD = re.compile(r"密码|验证码|校验码|动态码|卡号|cvv|cvc|password|passcode|pin\b|otp|verification|card", re.IGNORECASE)

MAX_ELEMENTS = 80
MAX_TEXTS = 60
TEXT_CHARS = 60


@dataclass(frozen=True)
class UiElement:
    ref: int
    kind: str  # button / input / scroll / text
    label: str
    bounds: tuple[int, int, int, int]  # 左、上、右、下（屏幕像素）
    password: bool = False
    resource_id: str = ""

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def secret(self) -> bool:
        return self.password or bool(SECRET_FIELD.search(f"{self.label} {self.resource_id}"))


@dataclass(frozen=True)
class Screen:
    package: str
    elements: tuple[UiElement, ...]
    texts: tuple[str, ...]  # 不能点的文字（看懂屏幕用）

    def get(self, ref: int) -> UiElement | None:
        return next((e for e in self.elements if e.ref == ref), None)

    def describe(self) -> str:
        """交给大模型的样子：在哪个 App、能操作的元素（带编号）、屏幕上的文字"""
        names = {"button": "按钮", "input": "输入框", "scroll": "可以滑动的区域"}
        lines = [f"现在在：{self.package or '（不知道哪个 App）'}"]
        if self.elements:
            lines.append("能操作的：")
            for e in self.elements:
                tag = names.get(e.kind, e.kind)
                extra = "（密码框，不能填）" if e.kind == "input" and e.secret else ""
                lines.append(f"[{e.ref}] {tag}「{e.label or '没有文字'}」{extra}")
        else:
            lines.append("屏幕上没认出能操作的东西（可能在加载，或者是游戏、视频这类画出来的界面）。")
        if self.texts:
            lines.append("屏幕上的文字：" + " / ".join(self.texts))
        return "\n".join(lines)


BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = BOUNDS.fullmatch(raw.strip())
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def _own_text(node: ElementTree.Element) -> str:
    return (node.get("text") or "").strip() or (node.get("content-desc") or "").strip()


def _all_text(node: ElementTree.Element) -> str:
    """这个元素和它里面的文字（可点的整块常常文字在子元素上）"""
    parts = [_own_text(n) for n in node.iter("node")]
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return " ".join(seen)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS] + "…"


def parse_screen(xml: str, package: str = "") -> Screen:
    """uiautomator dump 的 XML → 屏幕。可点的、能输入的、能滑的编上号；别的文字收起来当上下文"""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return Screen(package, (), ())
    elements: list[UiElement] = []
    texts: list[str] = []
    covered: set[int] = set()  # 已经算进某个可点元素里的节点（它的文字不再单独列）
    for node in root.iter("node"):
        if id(node) in covered:
            continue
        box = _bounds(node.get("bounds", ""))
        if box is None or node.get("visible-to-user", "true") == "false":
            continue
        pkg = node.get("package") or ""
        if not package and pkg:
            package = pkg
        cls = node.get("class") or ""
        editable = "EditText" in cls or "AutoCompleteTextView" in cls
        clickable = node.get("clickable") == "true" or node.get("long-clickable") == "true" or node.get("checkable") == "true"
        scrollable = node.get("scrollable") == "true"
        kind = "input" if editable else "button" if clickable else "scroll" if scrollable else ""
        if kind and len(elements) < MAX_ELEMENTS:
            if editable:
                label = _own_text(node) or (node.get("hint") or "").strip()
            elif kind == "button":
                label = _all_text(node)
            else:
                label = ""
            if kind == "button":
                for child in node.iter("node"):
                    covered.add(id(child))
            elements.append(UiElement(len(elements) + 1, kind, _short(label), box, node.get("password") == "true",
                                      (node.get("resource-id") or "").split("/")[-1]))
            continue
        own = _own_text(node)
        if own and len(texts) < MAX_TEXTS and _short(own) not in texts:
            texts.append(_short(own))
    return Screen(package, tuple(elements), tuple(texts))


def tap_needs_confirm(el: UiElement | None, package: str) -> bool:
    """点这个要不要先问你：不认识的（编号对不上）、钱和银行 App 里的、文字像会产生后果的"""
    if el is None:
        return True
    if any(package.startswith(p) for p in SENSITIVE_APPS):
        return True
    return bool(RISKY.search(f"{el.label} {el.resource_id}"))


def type_problem(el: UiElement | None, text: str) -> str | None:
    """能不能往这个元素里打字；不能的话说为什么"""
    if el is None:
        return "编号对不上：先 phone_read 看一下现在的屏幕。"
    if el.kind != "input":
        return f"[{el.ref}]「{el.label}」不是输入框。"
    if el.secret:
        return "这是密码、验证码或卡号之类的输入框，我不能填，请你自己在手机上填。"
    if len(text) > 2000:
        return "要输入的内容太长了。"
    return None


def input_text_arg(text: str) -> str | None:
    """adb shell input text 的参数（只认英文、数字和常见符号；空格写成 %s，shell 特殊字符转义）。
    有中文这类字符时返回 None：要靠手机上的 ADBKeyBoard 输入法"""
    if not text or any(ord(c) > 126 or ord(c) < 32 for c in text):
        return None
    out = []
    for c in text:
        if c == " ":
            out.append("%s")
        elif c in "\\'\"`$&|;<>()*?~!#[]{}%":
            out.append("\\" + c)
        else:
            out.append(c)
    return "".join(out)
