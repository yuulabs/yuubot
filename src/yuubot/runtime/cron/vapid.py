"""VAPID key management and web push delivery."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

if TYPE_CHECKING:
    from .notifications import PushSubscription

_log = logging.getLogger(__name__)


class VapidKeys(msgspec.Struct, frozen=True):
    public_key: str
    private_key_pem: str


def _keys_path(data_dir: Path) -> Path:
    return data_dir / "keys" / "vapid.json"


def _public_key_b64url(private_key: ec.EllipticCurvePrivateKey) -> str:
    public_bytes = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=")


def load_or_create_vapid_keys(data_dir: Path) -> VapidKeys:
    path = _keys_path(data_dir)
    if path.exists():
        return msgspec.json.decode(path.read_bytes(), type=VapidKeys)
    path.parent.mkdir(parents=True, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    keys = VapidKeys(_public_key_b64url(private_key), private_pem)
    path.write_bytes(msgspec.json.encode(keys))
    return keys


def vapid_public_key(data_dir: Path) -> str:
    return load_or_create_vapid_keys(data_dir).public_key


async def send_web_push(data_dir: Path, subscription: PushSubscription, payload: str) -> None:
    from pywebpush import WebPushException, webpush

    keys = load_or_create_vapid_keys(data_dir)
    subscription_info: dict[str, str | bytes | dict[str, str | bytes]] = {
        "endpoint": subscription.endpoint,
        "keys": {key: value for key, value in subscription.keys.items()},
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=keys.private_key_pem,
            vapid_claims={"sub": "mailto:admin@yuubot.local"},
        )
    except WebPushException as exc:
        _log.warning("web push failed for subscription %s: %s", subscription.id, exc)
