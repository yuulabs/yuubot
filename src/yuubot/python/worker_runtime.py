"""IPython worker runtime executed inside ipykernel subprocesses."""

from __future__ import annotations

import gc
import importlib
import os

import psutil
from IPython.core.getipython import get_ipython

RECYCLE_EXIT_CODE = 75

ip = get_ipython()
proc = psutil.Process(os.getpid())
_max_rss_bytes = int(os.environ.get("YUUBOT_WORKER_MAX_RSS_BYTES", str(2 * 1024**3)))


def reset_worker_namespace() -> None:
    ip.run_line_magic("reset", "-sf")
    gc.collect()


def maybe_recycle_worker() -> None:
    rss = proc.memory_info().rss
    if rss > _max_rss_bytes:
        os._exit(RECYCLE_EXIT_CODE)


def import_facades() -> None:
    facade_bootstrap = importlib.import_module("facade_bootstrap")
    facade_bootstrap.import_facades()


def bootstrap() -> None:
    import_facades()


def reset_or_recycle() -> None:
    reset_worker_namespace()
    import_facades()
    maybe_recycle_worker()
