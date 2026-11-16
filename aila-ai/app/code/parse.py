"""用 tree-sitter 把源码切成函数、方法、类，记下签名、注释和它调用了谁。

为什么按函数切、不按字数切：报错堆栈指向的是「某个函数的第几行」，按函数切才能精确定位到一块；
检索结果也是一个完整的函数，大模型看到的上下文是完整的，不会从函数中间断开。

支持 Python、JavaScript、TypeScript（含 TSX）、Java，以及 .vue 文件里的 <script>。
调用关系按名字记（tree-sitter 只有语法、没有类型信息），是近似的调用图。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from typing import Any

EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".vue": "vue",
}

MAX_FILE_BYTES = 400_000  # 更大的多半是生成的代码
MAX_AVG_LINE = 250  # 平均一行这么长：压缩过的代码，不看
MAX_CHUNK_CHARS = 6000
MAX_MODULE_CHARS = 3000
MAX_CALLS = 120

# 哪些节点算一块，以及它们叫什么
FUNCTION_TYPES = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "generator_function_declaration", "method_definition"},
    "typescript": {"function_declaration", "generator_function_declaration", "method_definition", "function_signature"},
    "java": {"method_declaration", "constructor_declaration"},
}
CLASS_TYPES = {
    "python": {"class_definition"},
    "javascript": {"class_declaration"},
    "typescript": {"class_declaration", "abstract_class_declaration", "interface_declaration", "enum_declaration"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
}
# 一个变量 / 字段的值是函数：const run = () => {} / run = function () {} / class 里的 run = () => {}
FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
CALL_TYPES = {"call", "call_expression", "new_expression", "method_invocation", "object_creation_expression"}
COMMENT_TYPES = {"comment", "line_comment", "block_comment"}


@dataclass
class Chunk:
    kind: str  # function / method / class / module / template（.vue 的模板）
    name: str
    qname: str  # 带上所在的类和外层函数：SyncService.saveMessages、run_agent.add_result
    start: int  # 行号，从 1 开始
    end: int
    signature: str
    doc: str
    text: str
    # 调用了谁：直接调用记名字（helper），对象上的方法调用前面加点（.clear）——后者不知道对象是什么类型，解析时更保守
    calls: list[str] = field(default_factory=list)
    # 所在文件和类是干什么的（文件开头的说明、类的注释）：函数自己没写注释时，靠它知道这块代码属于哪件事
    context: str = ""


def language_of(path: str) -> str | None:
    dot = path.rfind(".")
    return EXTENSIONS.get(path[dot:].lower()) if dot >= 0 else None


@cache
def _parser(lang: str) -> Any:
    from tree_sitter import Language, Parser

    if lang == "python":
        import tree_sitter_python as mod

        ptr = mod.language()
    elif lang == "javascript":
        import tree_sitter_javascript as mod

        ptr = mod.language()
    elif lang in ("typescript", "tsx"):
        import tree_sitter_typescript as mod

        ptr = mod.language_tsx() if lang == "tsx" else mod.language_typescript()
    elif lang == "java":
        import tree_sitter_java as mod

        ptr = mod.language()
    else:
        raise ValueError(lang)
    return Parser(Language(ptr))


def parse_file(path: str, source: bytes) -> list[Chunk]:
    lang = language_of(path)
    if lang is None or len(source) > MAX_FILE_BYTES:
        return []
    text = source.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and len(text) / len(lines) > MAX_AVG_LINE:
        return []
    stem = re.sub(r"\.[^.]+$", "", path.replace("\\", "/").rsplit("/", 1)[-1])

    if lang == "vue":
        chunks: list[Chunk] = []
        for script_lang, offset, body in _vue_scripts(text):
            chunks += _parse(script_lang, body.encode("utf-8"), stem, offset)
        template = _vue_template(text, stem)
        if template:
            for c in chunks:
                c.context = c.context or template.doc
            chunks.append(template)
        return chunks
    return _parse(lang, source, stem, 0)


def _vue_template(text: str, stem: str) -> Chunk | None:
    """.vue 的 <template>：界面长什么样（按钮、输入框写在这里），用到了哪些组件"""
    m = re.search(r"<template\b[^>]*>(.*)</template>", text, re.S)
    if not m:
        return None
    start = text.count("\n", 0, m.start()) + 1
    end = text.count("\n", 0, m.end()) + 1
    comments = " ".join(re.findall(r"<!--(.*?)-->", m.group(1), re.S))
    components = list(dict.fromkeys(re.findall(r"<([A-Z][\w]*)", m.group(1))))
    return Chunk("template", stem, f"{stem}.template", start, end, f"<template>（{stem} 的界面）",
                 _clean_doc(comments), _cap(m.group(0), MAX_CHUNK_CHARS), components)


def _vue_scripts(text: str) -> Iterator[tuple[str, int, str]]:
    """.vue 文件里的 <script> 块：语言、在文件里从第几行开始、内容"""
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, re.S):
        attrs = m.group(1)
        lang = "typescript" if re.search(r"lang=[\"']tsx?[\"']", attrs) else "javascript"
        offset = text.count("\n", 0, m.start(2))
        yield lang, offset, m.group(2)


def _parse(lang: str, source: bytes, stem: str, offset: int) -> list[Chunk]:
    tree = _parser(lang).parse(source)
    base = "typescript" if lang == "tsx" else lang
    walker = _Walker(base, source, offset)
    walker.visit(tree.root_node, [])
    module = walker.module_chunk(tree.root_node, stem)
    chunks = ([module] if module else []) + walker.chunks

    # 每块带上文件开头的说明和所在类的注释
    summary = _first_sentence(walker.module_doc(tree.root_node))
    if module:
        module.doc = walker.module_doc(tree.root_node)[:300]
    class_docs = {c.qname: _first_sentence(c.doc) for c in chunks if c.kind == "class" and c.doc}
    for c in chunks:
        if c.kind == "module":
            continue  # 模块块自己就带着这段说明
        owner = class_docs.get(c.qname.rsplit(".", 1)[0], "") if "." in c.qname else ""
        c.context = " | ".join(p for p in (summary, owner) if p and p != c.doc)
    return chunks


def _first_sentence(text: str, limit: int = 80) -> str:
    m = re.match(r"(.+?[。！？.!?：:])(\s|$)", text)
    return (m.group(1) if m else text)[:limit]


class _Walker:
    def __init__(self, lang: str, source: bytes, offset: int) -> None:
        self.lang = lang
        self.src = source
        self.offset = offset
        self.chunks: list[Chunk] = []
        self.top_ranges: list[tuple[int, int]] = []  # 顶层函数和类占的行（剩下的算模块顶层代码）
        self.functions = FUNCTION_TYPES[lang]
        self.classes = CLASS_TYPES[lang]
        self.all_lines = source.decode("utf-8", errors="replace").split("\n")

    def text(self, node: Any) -> str:
        return self.src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def visit(self, node: Any, scope: list[str], in_class: bool = False) -> None:
        for child in node.named_children:
            self.visit_node(child, scope, in_class)

    def visit_node(self, node: Any, scope: list[str], in_class: bool) -> None:
        t = node.type
        if t == "decorated_definition":  # Python 带装饰器的函数 / 类：块从装饰器那行开始
            inner = node.child_by_field_name("definition")
            if inner is not None:
                self.visit_def(inner, scope, outer=node, in_class=in_class)
            return
        if t in self.functions or t in self.classes:
            self.visit_def(node, scope, outer=node, in_class=in_class)
            return
        if self.lang in ("javascript", "typescript"):
            named = self.named_function_value(node)
            if named:
                name, fn = named
                self.add_function(node, fn, name, scope, in_class)
                return
        self.visit(node, scope, in_class)

    def named_function_value(self, node: Any) -> tuple[str, Any] | None:
        """const run = () => {}、class 里的 run = () => {}、exports.run = function () {}"""
        t = node.type
        if t in ("variable_declarator", "public_field_definition", "field_definition"):
            value = node.child_by_field_name("value")
            name = node.child_by_field_name("name") or node.child_by_field_name("property")
        elif t == "assignment_expression":
            value = node.child_by_field_name("right")
            left = node.child_by_field_name("left")
            name = left.child_by_field_name("property") if left is not None and left.type == "member_expression" else left
        else:
            return None
        if value is None or name is None or value.type not in FUNCTION_VALUES:
            return None
        return self.text(name), value

    def visit_def(self, node: Any, scope: list[str], outer: Any, in_class: bool) -> None:
        name_node = node.child_by_field_name("name")
        name = self.text(name_node) if name_node is not None else "(匿名)"
        if node.type in self.classes:
            self.add_class(outer, node, name, scope)
        else:
            self.add_function(outer, node, name, scope, in_class)

    def add_function(self, outer: Any, node: Any, name: str, scope: list[str], in_class: bool) -> None:
        kind = "method" if in_class else "function"
        qname = ".".join([*scope, name])
        body = node.child_by_field_name("body")
        start, end = self.lines(outer)
        if not scope:
            self.top_ranges.append((start, end))
        chunk = Chunk(
            kind, name, qname, start, end, self.signature(outer, body), self.doc(outer, body),
            _cap(self.text(outer), MAX_CHUNK_CHARS), self.calls(node),
        )
        self.chunks.append(chunk)
        if body is not None:
            self.visit(body, [*scope, name])  # 函数里定义的函数也单独成块

    def add_class(self, outer: Any, node: Any, name: str, scope: list[str]) -> None:
        start, end = self.lines(outer)
        if not scope:
            self.top_ranges.append((start, end))
        body = node.child_by_field_name("body")
        index = len(self.chunks)
        if body is not None:
            self.visit(body, [*scope, name], in_class=True)
        members = [c for c in self.chunks[index:] if c.qname.count(".") == len(scope) + 1]
        # 类这一块：类头、文档、字段，加上方法签名列表（方法本身各自成块，不重复放）
        first = min((m.start for m in members), default=end + 1)
        header = "\n".join(self.all_lines[start - 1 - self.offset:first - 1 - self.offset])
        listing = "\n".join("    " + m.signature for m in members)
        text = _cap(header.rstrip() + ("\n" + listing if listing else ""), MAX_CHUNK_CHARS)
        chunk = Chunk("class", name, ".".join([*scope, name]), start, end, self.signature(outer, body),
                      self.doc(outer, body), text, self.calls(node, own_only=True))
        self.chunks.insert(index, chunk)

    def lines(self, node: Any) -> tuple[int, int]:
        return node.start_point[0] + 1 + self.offset, node.end_point[0] + 1 + self.offset

    def signature(self, outer: Any, body: Any) -> str:
        """函数 / 类定义头：从开头到函数体之前，压成一行"""
        end = body.start_byte if body is not None else outer.end_byte
        head = self.src[outer.start_byte:end].decode("utf-8", errors="replace")
        if self.lang == "python":  # 函数头和函数体之间可能夹着注释
            head = "\n".join(re.sub(r"\s#.*$", "", line) for line in head.split("\n"))
        head = re.sub(r"\s+", " ", head).strip().rstrip("{:").strip()
        return head[:200]

    def doc(self, outer: Any, body: Any) -> str:
        """Python 的文档字符串；其他语言紧挨在定义前面的注释"""
        if self.lang == "python" and body is not None and body.named_children:
            first = body.named_children[0]
            if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
                return _clean_doc(self.text(first.named_children[0]))
        comments = []
        prev = outer.prev_named_sibling
        line = outer.start_point[0]
        while prev is not None and prev.type in COMMENT_TYPES and prev.end_point[0] >= line - 1:
            comments.insert(0, self.text(prev))
            line = prev.start_point[0]
            prev = prev.prev_named_sibling
        return _clean_doc("\n".join(comments))

    def calls(self, node: Any, own_only: bool = False) -> list[str]:
        """这块代码调用了哪些名字（不进入里面单独成块的函数和类）"""
        names: list[str] = []
        stack = list(node.named_children)
        while stack and len(names) < MAX_CALLS:
            n = stack.pop()
            if n.type in self.functions or n.type in self.classes or (own_only and n.type in FUNCTION_VALUES):
                continue
            if n.type in CALL_TYPES:
                name = self.callee(n)
                if name and name not in names:
                    names.append(name)
            stack.extend(n.named_children)
        return names

    def callee(self, call: Any) -> str | None:
        """被调用的名字；对象上的方法调用（obj.run()、this.run()）前面加点"""
        t = call.type
        attr = False
        if t == "method_invocation":
            target = call.child_by_field_name("name")
            attr = call.child_by_field_name("object") is not None
        elif t == "object_creation_expression":
            target = call.child_by_field_name("type")
        elif t == "new_expression":
            target = call.child_by_field_name("constructor")
        else:
            target = call.child_by_field_name("function")
        if target is None:
            return None
        if target.type in ("attribute", "member_expression"):
            obj = target.child_by_field_name("object")
            # self.x() / this.x()：调用的是同一个类里的方法，当作直接调用（解析时会优先同一个文件）
            attr = obj is None or self.text(obj) not in ("self", "this", "cls")
            target = target.child_by_field_name("attribute") or target.child_by_field_name("property")
        if target is None:
            return None
        name = re.sub(r"<.*", "", self.text(target)).split(".")[-1]  # 去掉泛型参数、包名
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
            return None
        return "." + name if attr else name

    def module_doc(self, root: Any) -> str:
        """文件开头的说明：Python 的模块文档字符串；其他语言文件最开头的注释"""
        children = root.named_children
        if not children:
            return ""
        first = children[0]
        if self.lang == "python":
            if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
                return _clean_doc(self.text(first.named_children[0]))
            return ""
        comments = []
        for node in children:
            if node.type not in COMMENT_TYPES:
                break
            comments.append(self.text(node))
        return _clean_doc("\n".join(comments))

    def module_chunk(self, root: Any, stem: str) -> Chunk | None:
        """不在任何函数和类里的顶层代码（导入、常量、脚本）合成一块"""
        covered = set()
        for start, end in self.top_ranges:
            covered.update(range(start, end + 1))
        kept = [
            (i + 1 + self.offset, line) for i, line in enumerate(self.all_lines)
            if i + 1 + self.offset not in covered and line.strip()
        ]
        if not kept:
            return None
        text = _cap("\n".join(line for _, line in kept), MAX_MODULE_CHARS)
        return Chunk("module", stem, stem, kept[0][0], kept[-1][0], f"{stem}（模块顶层代码）", "", text,
                     self.calls(root))


def _clean_doc(raw: str) -> str:
    text = re.sub(r'^[rbuRBU]*("""|\'\'\'|"|\')|("""|\'\'\'|"|\')$', "", raw.strip())
    text = re.sub(r"\s*\*/\s*$", "", text, flags=re.M)  # 同一行结尾的 */
    text = re.sub(r"^\s*(/\*\*?|\*/|\*|//+|#)\s?", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()[:300]


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…（太长，后面省略）"
