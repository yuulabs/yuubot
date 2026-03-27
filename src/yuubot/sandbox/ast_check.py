"""AST-level validation for sandbox Python code.

This is a best-effort filter for LLM-generated code, not a security sandbox.
It rejects common dangerous patterns (dunders, reflection, etc.) while
accepting a small compatibility subset of import syntax for approved modules.
It does NOT guarantee isolation against adversarial human-crafted code.
"""

from __future__ import annotations

import ast

ALLOWED_IMPORT_MODULES: frozenset[str] = frozenset({
    "math",
    "random",
    "re",
    "itertools",
    "collections",
    "functools",
    "operator",
    "statistics",
    "json",
    "string",
    "textwrap",
    "heapq",
    "bisect",
})

# Builtins / names that must never appear as call targets.
FORBIDDEN_CALLS: frozenset[str] = frozenset({
    "open",
    "eval",
    "exec",
    "compile",
    "input",
    "help",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "vars",
    "dir",
    "globals",
    "locals",
    "type",
    "object",
    "super",
    "breakpoint",
    "__import__",
})

# Statement-level AST nodes that are not allowed.
_FORBIDDEN_STMTS: frozenset[type] = frozenset({
    ast.ClassDef,
    ast.AsyncFunctionDef,
    ast.Try,
    ast.With,
    ast.Raise,
    ast.AsyncWith,
    ast.AsyncFor,
})

# Also block TryStar (3.11+) if it exists.
if hasattr(ast, "TryStar"):
    _FORBIDDEN_STMTS = _FORBIDDEN_STMTS | {ast.TryStar}

# Expression nodes that are not allowed.
_FORBIDDEN_EXPRS: frozenset[type] = frozenset({
    ast.Lambda,
})


class _Checker(ast.NodeVisitor):
    """Walks an AST and collects policy violations."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    # --- helpers ---

    def _add(self, msg: str) -> None:
        self.violations.append(msg)

    def _check_name(self, name: str, node: ast.AST) -> None:
        if "__" in name:
            self._add(f"dunder name not allowed: {name!r}")

    def _check_attr(self, attr: str, node: ast.AST) -> None:
        if "__" in attr:
            self._add(f"dunder attribute not allowed: .{attr}")
        elif attr.startswith("_"):
            self._add(f"private attribute not allowed: .{attr}")

    # --- visitors ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_IMPORT_MODULES:
                self._add(f"import not allowed: {alias.name!r}")
            if "." in alias.name:
                self._add(f"nested import not allowed: {alias.name!r}")
            if alias.asname:
                self._check_name(alias.asname, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self._add("relative import not allowed")
            return
        module = node.module or ""
        self._add(f"from-import not allowed: {module!r}")

    def visit_Name(self, node: ast.Name) -> None:
        self._check_name(node.id, node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_attr(node.attr, node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name and name in FORBIDDEN_CALLS:
            self._add(f"forbidden call: {name}()")
        self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) in _FORBIDDEN_STMTS:
            self._add(f"{type(node).__name__} not allowed")
        if type(node) in _FORBIDDEN_EXPRS:
            self._add(f"{type(node).__name__} not allowed")
        super().generic_visit(node)


def validate(source: str) -> list[str]:
    """Validate *source* against the sandbox AST policy.

    Returns a list of human-readable violation strings.
    An empty list means the code is allowed.
    """
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]

    checker = _Checker()
    checker.visit(tree)
    return checker.violations


class _ImportNormalizer(ast.NodeTransformer):
    """Rewrite allowed import statements to the injected globals model."""

    def visit_Import(self, node: ast.Import) -> ast.stmt | list[ast.stmt]:
        replacements: list[ast.stmt] = []
        for alias in node.names:
            if alias.asname:
                replacements.append(
                    ast.copy_location(
                        ast.Assign(
                            targets=[ast.Name(id=alias.asname, ctx=ast.Store())],
                            value=ast.Name(id=alias.name, ctx=ast.Load()),
                        ),
                        node,
                    )
                )
        return replacements


def normalize_imports(source: str) -> ast.Module:
    """Parse *source* and rewrite allowed imports to sandbox-compatible AST."""
    tree = ast.parse(source, mode="exec")
    tree = _ImportNormalizer().visit(tree)
    ast.fix_missing_locations(tree)
    return tree
