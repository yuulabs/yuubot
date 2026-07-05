from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pymupdf4llm  # type: ignore[import-untyped]


def to_markdown(
    path: str,
    *,
    pages: Sequence[int] | None = None,
    write_images_to: str | None = None,
    dpi: int = 150,
    **kwargs: Any,
) -> str:
    if write_images_to:
        Path(write_images_to).mkdir(parents=True, exist_ok=True)
    return str(pymupdf4llm.to_markdown(
        path,
        pages=pages,
        write_images_to=write_images_to,
        dpi=dpi,
        **kwargs,
    ))
