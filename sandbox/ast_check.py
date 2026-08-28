"""因子代码 AST 静态安全检查：白名单导入、危险能力封禁、复杂度上限。"""

from __future__ import annotations

import ast
import hashlib
from typing import Any

from models.factor import SandboxCheckResult

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "statistics"}

FORBIDDEN_BUILTINS = {
    "open",
    "eval",
    "exec",
    "compile",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "__import__",
    "help",
    "memoryview",
    "object",
    "staticmethod",
    "classmethod",
}

MAX_CODE_LENGTH = 12_000
MAX_NODES = 800
MAX_DEPTH = 80
MAX_FOR_LOOPS = 3


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.node_count = 0
        self.max_depth = 0
        self.for_loops = 0
        self.has_calculate = False
        self._depth = 0

    def generic_visit(self, node: ast.AST) -> None:
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        self.node_count += 1
        super().generic_visit(node)
        self._depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORTS:
                self.errors.append(f"禁止导入模块：{alias.name}")
            if alias.asname and not alias.asname.isidentifier():
                self.errors.append(f"非法导入别名：{alias.asname}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.errors.append("禁止使用 from ... import 语法")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_BUILTINS:
            self.errors.append(f"禁止使用内置能力：{node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        name = node.attr
        if "__" in name:
            self.errors.append(f"禁止访问魔法属性：{name}")
        elif name in FORBIDDEN_BUILTINS:
            self.errors.append(f"禁止调用危险方法：{name}")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.errors.append("禁止使用 while 循环")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.errors.append("禁止使用异步函数")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.errors.append("禁止使用异步循环")

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.errors.append("禁止使用异步上下文")

    def visit_With(self, node: ast.With) -> None:
        self.errors.append("禁止使用 with 语句")

    def visit_For(self, node: ast.For) -> None:
        self.for_loops += 1
        if self.for_loops > MAX_FOR_LOOPS:
            self.errors.append(f"for 循环数量超过上限（{MAX_FOR_LOOPS}）")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "calculate":
            self.has_calculate = True
            if len(node.args.args) < 2:
                self.errors.append("calculate 函数至少需要 df 与 params 两个参数")
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.errors.append("禁止使用 yield")

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            if name not in {"params"}:
                self.errors.append(f"禁止声明全局变量：{name}")


def check_source(code: str) -> SandboxCheckResult:
    """静态检查因子代码，返回检查结果（不执行代码）。"""

    if len(code) > MAX_CODE_LENGTH:
        return SandboxCheckResult(
            ok=False,
            errors=[f"代码长度超过上限（{MAX_CODE_LENGTH} 字符）"],
            warnings=[],
        )

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return SandboxCheckResult(
            ok=False,
            errors=[f"Python 语法错误：{exc.msg}（第 {exc.lineno} 行）"],
            warnings=[],
        )

    visitor = _Visitor()
    visitor.visit(tree)

    if not visitor.has_calculate:
        visitor.errors.append("缺少 calculate(df, params) 函数定义")
    if visitor.node_count > MAX_NODES:
        visitor.errors.append(f"AST 节点数超过上限（{MAX_NODES}）")
    if visitor.max_depth > MAX_DEPTH:
        visitor.errors.append(f"语法树深度超过上限（{MAX_DEPTH}）")

    return SandboxCheckResult(
        ok=not visitor.errors,
        errors=visitor.errors,
        warnings=visitor.warnings,
        node_count=visitor.node_count,
        max_depth=visitor.max_depth,
    )

