"""Facade package imports for ipykernel workers."""

from __future__ import annotations

from ..integrations.registry import default_registry

_RUNTIME_FACADE_PACKAGES = (
    "yb.fixer",
    "yb.conversations",
    "yb.tasks",
    "yb.tasks.cron",
    "yb.mcps",
    "yb.skills",
    "yb.office.pdf",
)

_ROOT_PACKAGES = ("yb", "yext")


def all_facade_packages() -> tuple[str, ...]:
    registry_packages = tuple(spec.package_path for spec in default_registry().specs().values())
    seen: set[str] = set()
    result: list[str] = []
    for package in (*_ROOT_PACKAGES, *registry_packages, *_RUNTIME_FACADE_PACKAGES):
        if package not in seen:
            seen.add(package)
            result.append(package)
    return tuple(result)


ALL_FACADE_PACKAGES = all_facade_packages()


def facade_bootstrap_code() -> str:
    return "".join(f"import {package}\n" for package in ALL_FACADE_PACKAGES)


def facade_bootstrap_module_source() -> str:
    bootstrap = facade_bootstrap_code().rstrip()
    return (
        '"""Auto-generated facade imports for ipykernel workers."""\n\n'
        "from __future__ import annotations\n\n"
        "from IPython.core.getipython import get_ipython\n\n"
        f"_BOOTSTRAP = {bootstrap!r}\n\n\n"
        "def import_facades() -> None:\n"
        "    get_ipython().run_cell(_BOOTSTRAP, store_history=False)\n"
    )
