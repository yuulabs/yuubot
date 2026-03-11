"""Media file downloader for multimodal messages."""

import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx

from loguru import logger


class MediaDownloader:
    def __init__(self, media_dir: str) -> None:
        self.media_dir = Path(media_dir).expanduser()
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def close(self) -> None:
        await self.client.aclose()

    async def download(self, url: str) -> str | None:
        """Download media file and return local path. Returns None on failure."""
        if not url:
            return None

        try:
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
            parsed = urlparse(url)
            ext_from_url = Path(parsed.path).suffix

            # If URL has a clear extension, try cache hit directly
            if ext_from_url:
                candidate = self.media_dir / f"{url_hash}{ext_from_url}"
                if candidate.exists():
                    logger.debug("Media already exists: {}", candidate)
                    return str(candidate)

            # Download file
            logger.info("Downloading media: {}", url)
            response = await self.client.get(url)
            response.raise_for_status()

            # Determine extension: URL path > Content-Type > fallback
            ext = ext_from_url
            if not ext:
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                ext = mimetypes.guess_extension(content_type) or ".bin"
            filename = f"{url_hash}{ext}"
            local_path = self.media_dir / filename

            if local_path.exists():
                logger.debug("Media already exists: {}", local_path)
                return str(local_path)

            local_path.write_bytes(response.content)
            logger.info("Saved media to: {}", local_path)
            return str(local_path)

        except Exception:
            logger.exception("Failed to download media: {}", url)
            return None
