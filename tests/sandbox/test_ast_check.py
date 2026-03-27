"""Tests for sandbox AST validation."""

import pytest

from yuubot.sandbox.ast_check import normalize_imports, validate


# --- allowed code ---


@pytest.mark.parametrize(
    "code",
    [
        "x = 1 + 2",
        "return_result(42)",
        "for i in range(10): pass",
        "while True: break",
        "x = [i**2 for i in range(5)]",
        "x = {k: v for k, v in enumerate('abc')}",
        "x = {i for i in range(3)}",
        "def add(a, b): return a + b",
        "x = sorted([3, 1, 2])",
        "x = list(filter(bool, [0, 1, 2]))",
        "if True:\n    x = 1\nelse:\n    x = 2",
        "x = math.sqrt(4)",
        "x = re.findall(r'\\d+', 'a1b2')",
        "x = collections.Counter([1, 1, 2])",
        "x = json.dumps({'a': 1})",
        "x = list(itertools.chain([1], [2]))",
        "import random",
        "import random as rnd",
        "import random, json",
        "return_result(sum(range(100)))",
        "x = 'hello'[1:3]",
        "a, b = 1, 2",
    ],
    ids=lambda c: c[:40],
)
def test_allowed_code(code: str) -> None:
    assert validate(code) == []


# --- import denied ---


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import sys",
        "from math import sqrt",
        "from os import path",
        "import subprocess",
        "import random.foo",
        "from random import randint",
    ],
)
def test_import_denied(code: str) -> None:
    violations = validate(code)
    assert any("import" in v for v in violations)


def test_normalize_import_drops_plain_allowed_import() -> None:
    tree = normalize_imports("import random\nreturn_result(random.randint(1, 6))")
    assert tree.body[0].__class__.__name__ == "Expr"


def test_normalize_import_rewrites_alias_import() -> None:
    tree = normalize_imports("import random as rnd\nreturn_result(rnd.randint(1, 6))")
    assign = tree.body[0]
    assert assign.__class__.__name__ == "Assign"
    assert assign.targets[0].id == "rnd"
    assert assign.value.id == "random"


# --- dunder denied ---


@pytest.mark.parametrize(
    "code",
    [
        "x = ().__class__",
        "x = x.__dict__",
        "x = x.__bases__",
        "x = __name__",
        "x = __builtins__",
    ],
)
def test_dunder_denied(code: str) -> None:
    violations = validate(code)
    assert any("dunder" in v for v in violations)


# --- private attribute denied ---


def test_private_attr_denied() -> None:
    violations = validate("x = obj._private")
    assert any("private" in v for v in violations)


def test_single_underscore_name_allowed() -> None:
    # _ as a variable name is fine (contains no __)
    assert validate("_ = 1") == []


# --- forbidden calls ---


@pytest.mark.parametrize(
    "code",
    [
        "open('x')",
        "eval('1')",
        "exec('1')",
        "compile('1', '', 'exec')",
        "getattr(x, 'y')",
        "setattr(x, 'y', 1)",
        "delattr(x, 'y')",
        "hasattr(x, 'y')",
        "vars()",
        "dir()",
        "globals()",
        "locals()",
        "type(1)",
        "object()",
        "super()",
        "breakpoint()",
        "input()",
        "help()",
    ],
)
def test_forbidden_calls(code: str) -> None:
    violations = validate(code)
    assert any("forbidden call" in v for v in violations)


# --- forbidden statements ---


@pytest.mark.parametrize(
    "code,keyword",
    [
        ("class Foo: pass", "ClassDef"),
        ("async def foo(): pass", "AsyncFunctionDef"),
        ("try:\n    pass\nexcept:\n    pass", "Try"),
        ("with open('x') as f: pass", "With"),
        ("raise ValueError", "Raise"),
    ],
)
def test_forbidden_statements(code: str, keyword: str) -> None:
    violations = validate(code)
    assert any(keyword in v for v in violations)


# --- lambda denied ---


def test_lambda_denied() -> None:
    violations = validate("f = lambda x: x + 1")
    assert any("Lambda" in v for v in violations)


# --- syntax errors ---


def test_syntax_error() -> None:
    violations = validate("def (invalid")
    assert any("syntax error" in v for v in violations)
