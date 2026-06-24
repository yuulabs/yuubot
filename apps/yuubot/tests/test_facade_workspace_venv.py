"""Facade workspace venv provisioning — isolated Python environment per actor."""

from __future__ import annotations

import subprocess
from pathlib import Path

from yuubot.core.facade.workspace import FacadeEndpoint, FacadeWorkspace


def test_bind_actor_provisions_isolated_venv(tmp_path: Path) -> None:
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    binding = ws.bind_actor(
        actor_id="actor-1",
        agent_name="a",
        session_id="s",
        mailbox_id="m",
        capabilities=(),
        endpoint=FacadeEndpoint(host="127.0.0.1", port=1, token="t"),
    )

    assert binding.venv_python is not None
    venv_python = Path(binding.venv_python)
    assert venv_python.exists(), f"venv python not found at {venv_python}"
    assert ".venv" in venv_python.parts

    out = subprocess.run(
        [str(venv_python), "-c", "import pandas, numpy, matplotlib; print('ok')"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr


def test_bind_actor_venv_imports_facade(tmp_path: Path) -> None:
    """The actor venv + binding sys_path must let the facade imports resolve.

    The kernel bootstrap runs ``import yb; import tim; import yext.github`` plus
    ``import facade_context`` unconditionally. For that to work on the isolated
    actor ``.venv`` (not the daemon venv), two things must hold:
    ``msgspec`` (the facade's only third-party dep) is installed in the actor
    venv, and the daemon's ``apps/yuubot/src`` is on the binding ``sys_path``.
    """
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    binding = ws.bind_actor(
        actor_id="actor-1",
        agent_name="a",
        session_id="s",
        mailbox_id="m",
        capabilities=(),
        endpoint=FacadeEndpoint(host="127.0.0.1", port=1, token="t"),
    )
    assert binding.venv_python is not None

    import os

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(binding.sys_path))
    out = subprocess.run(
        [binding.venv_python, "-c",
         "import yb, tim, yext.github, msgspec, pandas; print('ok')"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_bind_actor_is_idempotent(tmp_path: Path) -> None:
    """Re-binding an already-provisioned actor with the same deps must not sync.

    The idempotency fast path (``venv_python`` exists AND the on-disk
    ``pyproject.toml`` matches the desired dependency declaration) must return
    without rewriting the pyproject or re-running ``uv sync``. That is what
    makes a no-op re-bind of an actor between daemon restarts cheap.

    Probe: ``uv sync`` (re)writes ``uv.lock`` in the project dir every time it
    runs; the fast path returns before invoking ``uv sync`` and therefore never
    touches ``uv.lock``. So: delete ``uv.lock`` after the first bind, then
    re-bind with unchanged deps and assert ``uv.lock`` is NOT recreated — that
    proves the fast path was taken and ``uv sync`` did not run. (Phase 1's
    earlier "delete the pyproject to prove no rewrite" probe is invalid under
    the content-aware guard introduced for facade-dep upgrades: a missing
    pyproject is exactly the drift case the guard must reconcile, see
    ``test_bind_actor_resyncs_when_pyproject_drifts``.)
    """
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    endpoint = FacadeEndpoint(host="127.0.0.1", port=1, token="t")

    first = ws.bind_actor(
        actor_id="actor-2",
        agent_name="a",
        session_id="s1",
        mailbox_id="m",
        capabilities=(),
        endpoint=endpoint,
    )
    assert first.venv_python is not None
    venv_python = Path(first.venv_python)
    venv_mtime_before = venv_python.stat().st_mtime_ns

    pyproject = first.root / "pyproject.toml"
    uv_lock = first.root / "uv.lock"
    assert pyproject.exists()
    assert uv_lock.exists(), "first bind must have run uv sync (which writes uv.lock)"
    original_pyproject = pyproject.read_text(encoding="utf-8")
    # Delete uv.lock — if the re-bind re-runs uv sync, it would be recreated.
    uv_lock.unlink()

    second = ws.bind_actor(
        actor_id="actor-2",
        agent_name="a",
        session_id="s2",
        mailbox_id="m",
        capabilities=(),
        endpoint=endpoint,
    )

    # Same venv python path, venv not rebuilt.
    assert second.venv_python == first.venv_python
    assert venv_python.exists()
    assert venv_python.stat().st_mtime_ns == venv_mtime_before
    # uv.lock must NOT have been recreated → uv sync did not run (fast path).
    assert not uv_lock.exists(), "idempotent re-bind must not re-run uv sync"
    # And the pyproject is left byte-identical (fast path returns before write).
    assert pyproject.read_text(encoding="utf-8") == original_pyproject


def test_bind_actor_resyncs_when_pyproject_drifts(tmp_path: Path) -> None:
    """Re-binding after the pyproject drifted must re-run uv sync.

    Complement of the idempotent test. When the on-disk ``pyproject.toml``
    differs from the desired dependency declaration (e.g. the daemon was
    upgraded to add a new facade dep like ``msgspec`` and the actor's recorded
    pyproject predates the upgrade), the idempotency guard must NOT take the
    fast path: it must rewrite the pyproject to the desired declaration and
    re-run ``uv sync`` so the existing venv is reconciled. This is the
    contract that lets a facade-dep upgrade propagate to already-provisioned
    actors on their next re-bind without nuking the venv.

    The reconciliation probe is content-based (robust against coarse-fs mtime):
    the fast path returns before any write, so if the stale content survived a
    re-bind that would mean the drift path was skipped. Asserting the on-disk
    pyproject is restored to the desired declaration proves the drift path ran.
    """
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    endpoint = FacadeEndpoint(host="127.0.0.1", port=1, token="t")

    first = ws.bind_actor(
        actor_id="actor-3",
        agent_name="a",
        session_id="s1",
        mailbox_id="m",
        capabilities=(),
        endpoint=endpoint,
    )
    assert first.venv_python is not None
    venv_python = Path(first.venv_python)

    pyproject = first.root / "pyproject.toml"
    uv_lock = first.root / "uv.lock"
    assert pyproject.exists()
    desired_pyproject = pyproject.read_text(encoding="utf-8")
    assert '"msgspec"' in desired_pyproject

    # Simulate a pre-upgrade stale pyproject: one missing the new facade dep.
    # (A bare dep name swap keeps the TOML structurally valid so the rewrite
    # path doesn't have to fight a parse error; only the *content* differs
    # from the desired declaration, which is what the guard checks.)
    stale_pyproject = desired_pyproject.replace('"msgspec"', '"legacy-facade-dep"')
    assert stale_pyproject != desired_pyproject
    pyproject.write_text(stale_pyproject, encoding="utf-8")
    # Also drop uv.lock so the drift path recreating it is observable.
    uv_lock.unlink()
    assert not uv_lock.exists()

    second = ws.bind_actor(
        actor_id="actor-3",
        agent_name="a",
        session_id="s2",
        mailbox_id="m",
        capabilities=(),
        endpoint=endpoint,
    )

    # The guard detected drift and reconciled: pyproject rewritten to the
    # desired declaration (with msgspec, not the stale legacy dep). Under the
    # fast path the stale content would have survived untouched.
    assert second.venv_python == first.venv_python
    assert venv_python.exists()
    restored = pyproject.read_text(encoding="utf-8")
    assert restored == desired_pyproject
    assert '"msgspec"' in restored
    assert '"legacy-facade-dep"' not in restored
    # And uv sync ran (drift path) → uv.lock recreated.
    assert uv_lock.exists(), "drifted re-bind must re-run uv sync (writes uv.lock)"
