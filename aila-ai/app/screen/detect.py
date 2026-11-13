"""从屏幕上的文字里认出报错，截出相关的一段，去掉密钥这类敏感内容。

规则按「错误信息长什么样」写，不是按关键词：编辑器里的代码 `raise ValueError(...)`、`except KeyError:`
不会命中，终端里的 `ValueError: invalid literal...` 才会。各语言的规则可以继续往下加。
"""

import hashlib
import re
from dataclasses import dataclass

MAX_SNIPPET_CHARS = 1500
MAX_SUMMARY_CHARS = 120
BEFORE, AFTER, MAX_LINES = 3, 15, 40


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    # 这一行本身就是错误信息（比如 TypeError: ...），摘要优先用它；否则只是「出错了」的标志（比如 Traceback、BUILD FAILED）
    message: bool


def _rule(name: str, pattern: str, message: bool, flags: int = 0) -> Rule:
    return Rule(name, re.compile(pattern, flags), message)


RULES = [
    _rule("python", r"^Traceback \(most recent call last\)", False),
    # TypeError: ... / ValueError: ... / java.lang.NullPointerException / KeyError: 'x'
    _rule("exception", r"^(?:[\w$]+\.)*[A-Z]\w*(?:Error|Exception)(?::\s*\S|$)", True),
    _rule("uncaught", r"Uncaught (?:\(in promise\) )?\w*(?:Error|Exception)", True),
    _rule("typescript", r"\berror TS\d{3,5}\b|\bts\(\d{3,5}\)", True, re.I),
    _rule("npm", r"\bnpm (?:ERR!|error )", True, re.I),
    _rule("java", r"Exception in thread \"|^Caused by: [\w.$]+(?:Exception|Error)", True),
    _rule("maven", r"^\[ERROR\] \S", True),
    _rule("build", r"BUILD FAILURE|BUILD FAILED|FAILURE: Build failed|\bBuild failed\b|编译失败|构建失败", False),
    _rule("rust", r"^error(?:\[E\d{4}\])?: \S", True),
    _rule("go", r"^panic: \S", True),
    _rule("c", r"\berror C\d{4}:|: (?:fatal )?error: \S", True),
    _rule("test", r"^FAILED \S|^FAIL \S|\b[1-9]\d* failed\b", False),
]

SECRETS = [
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer ***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
    (re.compile(r"(?i)\b(authorization|cookie)(\s*:\s*).+"), r"\1\2***"),  # 整个请求头的值
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd)(\s*[:=]\s*)(\S+)"), r"\1\2***"),
    # 很长的一串、字母数字混着的（令牌、密钥）；不含 / 和 .，路径、网址不受影响；纯字母的长函数名也不受影响
    (re.compile(r"\b(?=[A-Za-z0-9+_\-]*\d)(?=[A-Za-z0-9+_\-]*[A-Za-z])[A-Za-z0-9+_\-]{32,}={0,2}"), "***"),
]


@dataclass(frozen=True)
class Finding:
    rule: str
    summary: str  # 一行，给用户看「是哪个错」
    snippet: str  # 报错附近的几行，用户同意后才交给大模型
    signature: str  # 同一个错误（行号变了也算同一个）只问一次


def match(line: str) -> Rule | None:
    text = line.strip()
    return next((r for r in RULES if r.pattern.search(text)), None)


def detect(lines: list[str]) -> Finding | None:
    hit = next(((i, r) for i, line in enumerate(lines) if (r := match(line))), None)
    if hit is None:
        return None
    i, rule = hit

    # 摘要：命中那行之后第一个「错误信息」行（Python 的错误信息在 Traceback 最后）
    end = min(len(lines), i + 1 + AFTER)
    summary_at = i if rule.message else next(
        (j for j in range(i, min(len(lines), i + MAX_LINES)) if (r := match(lines[j])) and r.message), i
    )
    end = max(end, min(summary_at + 2, i + MAX_LINES, len(lines)))
    snippet = redact("\n".join(lines[max(0, i - BEFORE):end]))
    if len(snippet) > MAX_SNIPPET_CHARS:
        snippet = snippet[:MAX_SNIPPET_CHARS] + "\n…"
    summary = redact(lines[summary_at].strip())[:MAX_SUMMARY_CHARS]
    return Finding(rule.name, summary, snippet, signature(summary))


def signature(summary: str) -> str:
    """忽略数字、引号里的内容和空白：同一个错误换了行号、换了变量值，算同一个"""
    norm = re.sub(r"(['\"]).*?\1", "''", summary.lower())
    norm = re.sub(r"\d+", "#", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def redact(text: str) -> str:
    for pattern, repl in SECRETS:
        text = pattern.sub(repl, text)
    return text
