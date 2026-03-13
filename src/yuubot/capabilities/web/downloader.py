"""File downloader — download URLs to local folder."""

import os
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx

from yuubot.config import load_config
from .blocklist import check_url


async def download_urls(urls_str: str, folder: str, config_path: str | None) -> None:
    """Download one or more URLs (newline-separated) to folder."""
    load_config(config_path)

    urls = [u.strip() for u in urls_str.strip().splitlines() if u.strip()]
    if not urls:
        click.echo("没有提供 URL")
        return

    dest = Path(folder)
    dest.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for url in urls:
            try:
                check_url(url)
            except ValueError as e:
                click.echo(f"[✗] {url} — {e}")
                continue
            try:
                r = await client.get(url)
                r.raise_for_status()

                # Determine filename
                parsed = urlparse(url)
                fname = os.path.basename(parsed.path) or "download"
                if not Path(fname).suffix:
                    ct = r.headers.get("content-type", "")
                    if "html" in ct:
                        fname += ".html"
                    elif "json" in ct:
                        fname += ".json"

                out_path = dest / fname
                # Avoid overwrite
                counter = 1
                while out_path.exists():
                    stem = Path(fname).stem
                    suffix = Path(fname).suffix
                    out_path = dest / f"{stem}_{counter}{suffix}"
                    counter += 1

                out_path.write_bytes(r.content)
                click.echo(f"[✓] {url} → {out_path}")
            except Exception as e:
                click.echo(f"[✗] {url} — {e}")
