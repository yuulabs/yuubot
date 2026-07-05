"""Configuration for the ipykernel worker pool."""

import msgspec

DEFAULT_MAX_WORKERS = 4
DEFAULT_ACQUIRE_TIMEOUT_S = 30.0
DEFAULT_MAX_RSS_BYTES = 2 * 1024**3
DEFAULT_IDLE_TTL_S = 6 * 3600
RECYCLE_EXIT_CODE = 75


class PythonKernelsConfig(msgspec.Struct, frozen=True, kw_only=True):
    max_workers: int = DEFAULT_MAX_WORKERS
    acquire_timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT_S
    max_rss_bytes: int = DEFAULT_MAX_RSS_BYTES
    idle_ttl_s: float = DEFAULT_IDLE_TTL_S


def python_kernels_config_from_raw(raw: object) -> PythonKernelsConfig:
    if not isinstance(raw, dict):
        return PythonKernelsConfig()
    return msgspec.convert(raw, PythonKernelsConfig)
