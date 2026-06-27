"""PDF helpers backed by pymupdf4llm."""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any, Sequence

import pymupdf4llm


def to_markdown(
    path: str | PathLike[str],
    *,
    pages: Sequence[int] | None = None,
    write_images_to: str | PathLike[str] | None = None,
    dpi: int = 150,
    **kwargs: Any,
) -> Any:
    """Convert a PDF to markdown using ``pymupdf4llm.to_markdown``.

    Args:
        path: PDF path.
        pages: Optional 0-based page indexes to convert, e.g. ``[0, 1, 2]``.
        write_images_to: Optional directory for extracted images. Images are
            written as PNG files and referenced from the markdown.
        dpi: Image extraction resolution. Defaults to 150.
        **kwargs: Additional ``pymupdf4llm.to_markdown`` keyword arguments.

    The return value is exactly whatever pymupdf4llm returns for the supplied
    options.
    """
    if write_images_to is not None:
        image_dir = Path(write_images_to)
        image_dir.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault("write_images", True)
        kwargs.setdefault("image_path", str(image_dir))
        kwargs.setdefault("image_format", "png")
    if pages is not None:
        kwargs.setdefault("pages", list(pages))
    kwargs.setdefault("dpi", dpi)
    return pymupdf4llm.to_markdown(path, **kwargs)


__all__ = ["to_markdown"]
