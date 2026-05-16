"""Runtime secret wrappers and encryption helpers."""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any, TypeGuard, cast, get_args, get_origin

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRET_FORMAT = {"type": "string", "format": "secret"}
ENCRYPTED_SECRET_VERSION = "v1"


class Secret:
    """A sensitive string that never prints its plaintext by accident."""

    __slots__ = ("_plaintext",)

    def __init__(self, plaintext: str) -> None:
        self._plaintext = plaintext

    def reveal(self) -> str:
        return self._plaintext

    def __str__(self) -> str:
        return "***"

    def __repr__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return False
        return self.reveal() == other.reveal()


class SecretCodec:
    """Encrypt and decrypt secret values with a 32-byte base64 master key."""

    def __init__(self, master_key: str) -> None:
        self._aead = AESGCM(_decode_master_key(master_key))

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()

    def decrypt(self, ciphertext: str) -> str:
        payload = base64.b64decode(ciphertext.encode(), validate=True)
        if len(payload) < 13:
            raise ValueError("secret ciphertext is too short")
        nonce = payload[:12]
        body = payload[12:]
        return self._aead.decrypt(nonce, body, None).decode()

    @staticmethod
    def mask(ciphertext: str) -> str:
        return f"set:{len(ciphertext)}"


def encrypt_secret_values(value: object, codec: SecretCodec) -> object:
    if isinstance(value, Secret):
        return {
            "$enc": ENCRYPTED_SECRET_VERSION,
            "ct": codec.encrypt(value.reveal()),
        }
    if isinstance(value, dict):
        return {key: encrypt_secret_values(item, codec) for key, item in value.items()}
    if isinstance(value, list):
        return [encrypt_secret_values(item, codec) for item in value]
    if isinstance(value, tuple):
        return [encrypt_secret_values(item, codec) for item in value]
    return value


def decrypt_secret_values(value: object, codec: SecretCodec) -> object:
    if _is_encrypted_secret(value):
        return Secret(codec.decrypt(value["ct"]))
    if isinstance(value, dict):
        return {key: decrypt_secret_values(item, codec) for key, item in value.items()}
    if isinstance(value, list):
        return [decrypt_secret_values(item, codec) for item in value]
    return value


def redact_secret_for_json(value: object) -> object:
    if isinstance(value, Secret):
        return "***"
    raise TypeError(f"unsupported value {type(value).__name__}")


def secret_schema_hook(schema_type: type) -> dict[str, Any]:
    if schema_type is Secret:
        return dict(SECRET_FORMAT)
    raise NotImplementedError


def secret_decode_hook(target_type: type, value: object) -> object:
    if target_type is Secret:
        if isinstance(value, Secret):
            return value
        if isinstance(value, str):
            return Secret(value)
        raise TypeError("secret fields must be strings")
    raise TypeError(f"unsupported target type {target_type!r}")


def is_secret_type(field_type: object) -> bool:
    if field_type is Secret:
        return True
    origin = get_origin(field_type)
    if origin is None:
        return False
    return any(is_secret_type(arg) for arg in get_args(field_type))


def master_key_is_valid(value: str) -> bool:
    try:
        _decode_master_key(value)
    except ValueError:
        return False
    return True


def master_key_for_tests() -> str:
    return base64.b64encode(b"yuubot-test-master-key-32-bytes!").decode()


def wrap_config_secrets(
    config: dict[str, object],
    *,
    schema: type | None,
    existing: dict[str, object] | None = None,
) -> dict[str, object]:
    """Validate an integration config schema and wrap secret fields."""

    if schema is None:
        return dict(config)

    from msgspec import Struct, ValidationError, convert
    from msgspec.structs import fields

    if not isinstance(schema, type) or not issubclass(schema, Struct):
        return dict(config)

    try:
        convert(config, type=schema, strict=False, dec_hook=secret_decode_hook)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    wrapped = dict(config)
    previous = existing or {}
    for field in fields(schema):
        if not is_secret_type(field.type):
            continue
        if _should_keep_existing_secret(field.name, wrapped, previous):
            wrapped[field.name] = previous[field.name]
            continue
        if field.name in wrapped and not isinstance(wrapped[field.name], Secret):
            value = wrapped[field.name]
            if not isinstance(value, str):
                raise ValueError(f"secret field {field.name!r} must be a string")
            wrapped[field.name] = Secret(value)
    return wrapped


def secret_field_names(schema: type | None) -> tuple[str, ...]:
    from msgspec import Struct
    from msgspec.structs import fields

    if not isinstance(schema, type) or not issubclass(schema, Struct):
        return ()
    return tuple(field.name for field in fields(schema) if is_secret_type(field.type))


def _should_keep_existing_secret(
    field_name: str,
    config: dict[str, object],
    existing: dict[str, object],
) -> bool:
    if field_name not in existing:
        return False
    if field_name not in config:
        return True
    return config[field_name] == ""


def _is_encrypted_secret(value: object) -> TypeGuard[dict[str, str]]:
    if not isinstance(value, dict):
        return False
    candidate = cast(dict[object, object], value)
    return (
        candidate.get("$enc") == ENCRYPTED_SECRET_VERSION
        and isinstance(candidate.get("ct"), str)
    )


def _decode_master_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode(), validate=True)
    except binascii.Error as exc:
        raise ValueError("secrets.master_key must be base64") from exc
    if len(decoded) != 32:
        raise ValueError("secrets.master_key must decode to 32 bytes")
    return decoded
