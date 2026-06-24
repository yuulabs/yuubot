"""Pin the PEP 562 lazy-barrel contract on ``yuubot.core.facade``.

Background: ``yuubot.core.facade.__init__`` used to eagerly import ``.bridge``,
``.workspace``, ``.protocol``, ``.context``. ``.bridge`` pulls in ``yuullm``
(and through it the daemon's full LLM/HTTP/ORM stack — openai, anthropic,
tortoise-orm, httpx, ...). The isolated actor kernel — which runs on its own
``.venv`` with only ``msgspec`` + the data stack — must be able to import the
facade's msgspec-only submodules (``yb/_client`` imports
``yuubot.core.facade.protocol``) without dragging the daemon runtime stack in.
Eager submodule re-exports in the package ``__init__`` broke that isolation.
These tests pin the lazification so it does not silently regress.
"""

from __future__ import annotations

import sys


def test_importing_facade_package_does_not_import_yuullm() -> None:
    """``import yuubot.core.facade`` must stay msgspec-only.

    Importing the package (running its ``__init__``) must NOT eagerly import the
    ``.bridge`` submodule — if it did, ``yuullm`` would land in ``sys.modules``
    even though nothing the agent kernel needs required it. The agent kernel
    runs on an actor ``.venv`` that does not have ``yuullm``; an eager import
    there would raise ``ModuleNotFoundError`` and crash the kernel bootstrap.
    """
    # Drop any prior resident so the assertion is meaningful even on re-runs.
    for mod in [
        "yuubot.core.facade",
        "yuubot.core.facade.bridge",
        "yuullm",
    ]:
        sys.modules.pop(mod, None)

    import yuubot.core.facade as facade  # noqa: F401

    assert "yuullm" not in sys.modules, (
        "importing yuubot.core.facade eagerly imported yuullm; the package "
        "__init__ must lazify its submodule re-exports (PEP 562)"
    )
    assert "yuubot.core.facade.bridge" not in sys.modules, (
        "importing yuubot.core.facade eagerly imported the bridge submodule; "
        "submodule re-exports must be lazy (PEP 562)"
    )


def test_barrel_attribute_still_resolves_on_demand() -> None:
    """Daemon-side ``from yuubot.core.facade import X`` must keep working.

    The lazification is an implementation detail of the *package import*; the
    public barrel API is unchanged. Each name previously eagerly re-exported
    must still resolve on first attribute access (now triggering the submodule
    import lazily, in the daemon process where ``yuullm`` is available).
    """
    import yuubot.core.facade as facade

    # A bridge name — the heaviest submodule. Resolving it proves the lazy
    # __getattr__ path works end-to-end (and will pull in yuullm here, in the
    # daemon test process, which has it installed).
    assert facade.IntegrationInvokeBridge is not None
    # A protocol name — the msgspec-only path the agent kernel uses indirectly.
    assert facade.FacadeRpcRequest is not None
    # A workspace name — the type bind_actor returns to assembly.
    assert facade.FacadeWorkspace is not None


def test_dir_lists_full_public_surface() -> None:
    """``dir()`` on the package must still advertise every reexported name.

    Tooling (REPLs, IDEs, static analysis helpers) that introspects the barrel
    relies on ``__dir__`` being accurate. If a name silently dropped out of the
    lazy mapping, the barrel API would have shrunk without notice.
    """
    import yuubot.core.facade as facade

    public = dir(facade)
    for name in (
        "IntegrationInvokeBridge",
        "FacadeBackgroundTaskEnded",
        "FacadeBackgroundTaskStarted",
        "FacadeDelegateTask",
        "FACADE_CONTEXT_MODULE",
        "render_context_module",
        "DelegateSubmitPayload",
        "FacadeRpcRequest",
        "FacadeRpcResponse",
        "ImSendPayload",
        "RpcError",
        "ActorFacadeBinding",
        "FacadeEndpoint",
        "FacadeWorkspace",
        "YEXT_PACKAGE",
        "facade_call_path",
    ):
        assert name in public, f"{name!r} missing from dir(yuubot.core.facade)"
