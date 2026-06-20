"""Generated integration facade package tests."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

from yuubot.core.facade.codegen import clear_facade_module_cache, write_facade_package
from yuubot.core.integrations.impls.github.integration import (
    GITHUB_ISSUE_LIST_CAPABILITY_SPEC,
)


def test_nested_facade_module_imports_package_root_client(tmp_path: Path) -> None:
    write_facade_package(
        tmp_path,
        capabilities=(GITHUB_ISSUE_LIST_CAPABILITY_SPEC,),
    )
    sys.path.insert(0, str(tmp_path))
    sys.modules["yuubot_facade_context"] = types.ModuleType("yuubot_facade_context")
    clear_facade_module_cache()
    try:
        issue = importlib.import_module("yext.github.issue")

        assert callable(issue.list)
    finally:
        clear_facade_module_cache()
        sys.modules.pop("yuubot_facade_context", None)
        sys.path.remove(str(tmp_path))
