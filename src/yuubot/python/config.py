"""Configuration for the ipykernel worker pool."""

import msgspec

DEFAULT_MAX_WORKERS = 4
DEFAULT_ACQUIRE_TIMEOUT_S = 30.0
DEFAULT_MAX_RSS_BYTES = 2 * 1024**3
DEFAULT_IDLE_TTL_S = 6 * 3600
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_EXECUTION_TIMEOUT_S = 120.0
RECYCLE_EXIT_CODE = 75


class PythonKernelsConfig(msgspec.Struct, frozen=True):
    max_workers: int = DEFAULT_MAX_WORKERS
    acquire_timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT_S
    max_rss_bytes: int = DEFAULT_MAX_RSS_BYTES
    idle_ttl_s: float = DEFAULT_IDLE_TTL_S
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    execution_timeout_s: float = DEFAULT_EXECUTION_TIMEOUT_S


def python_kernels_config_from_raw(raw: object) -> PythonKernelsConfig:
    if not isinstance(raw, dict):
        return PythonKernelsConfig()
    return msgspec.convert(raw, PythonKernelsConfig)
