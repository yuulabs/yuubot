"""Provider-side image URL normalization."""

from __future__ import annotations

import base64
import mimetypes
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote, urlparse


_MAX_CACHE_BYTES = 50 * 1024 * 1024
_file_image_cache: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_file_image_cache_bytes = 0


def image_url_for_provider(url: str) -> str:
    if url.startswith("data:"):
        return url
    if url.startswith("file://"):
        return _file_image_data_url(url)
    return url


def _file_image_data_url(url: str) -> str:
    path = _file_url_path(url)
    stat = path.stat()
    key = (url, stat.st_mtime_ns, stat.st_size)
    cached = _file_image_cache.get(key)
    if cached is not None:
        _file_image_cache.move_to_end(key)
        return cached
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"
    _cache_file_image(key, data_url)
    return data_url


def _cache_file_image(key: tuple[str, int, int], data_url: str) -> None:
    global _file_image_cache_bytes
    size = len(data_url.encode("utf-8"))
    if size > _MAX_CACHE_BYTES:
        return
    previous = _file_image_cache.pop(key, None)
    if previous is not None:
        _file_image_cache_bytes -= len(previous.encode("utf-8"))
    _file_image_cache[key] = data_url
    _file_image_cache_bytes += size
    while _file_image_cache_bytes > _MAX_CACHE_BYTES:
        _old_key, old_value = _file_image_cache.popitem(last=False)
        _file_image_cache_bytes -= len(old_value.encode("utf-8"))


def _file_url_path(url: str) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported image URL scheme: {parsed.scheme!r}")
    if parsed.netloc not in {"", "localhost"}:
        raise ValueError(f"unsupported file URL host: {parsed.netloc!r}")
    return Path(unquote(parsed.path))
