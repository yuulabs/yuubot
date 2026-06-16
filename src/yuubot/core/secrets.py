"""Runtime secret wrappers and encryption helpers."""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any, cast, get_args, get_origin

import msgspec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRET_FORMAT = {"type": "string", "format": "secret"}
ENCRYPTED_SECRET_VERSION = "v1"


class EncryptedSecret(msgspec.Struct):
    """A typed encrypted secret marker — validated once at the boundary, then trusted downstream."""

    enc: str = msgspec.field(name="$enc")
    ct: str


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


def secret_enc_hook(value: object, codec: SecretCodec) -> object:
    """Single-value encryption hook for msgspec enc_hook.

    Converts Secret → EncryptedSecret. msgspec handles the traversal;
    this function only transforms individual values.
    """
    if isinstance(value, Secret):
        return EncryptedSecret(
            enc=ENCRYPTED_SECRET_VERSION,
            ct=codec.encrypt(value.reveal()),
        )
    raise TypeError(f"secret_enc_hook: unsupported type {type(value).__name__}")


def secret_dec_hook(target_type: type, value: object, codec: SecretCodec) -> object:
    """Single-value decryption hook for msgspec dec_hook.

    Converts EncryptedSecret → Secret. msgspec handles the traversal;
    this function only transforms individual values.

    Also handles the JSON-deserialized form: a dict with ``$enc`` and ``ct``
    keys is recognized as an encrypted secret and decrypted in place.

    When ``target_type`` is ``object``, encrypted secrets are still decrypted
    so they don't leak through as opaque structs in loosely-typed fields.
    """
    if target_type is Secret:
        if isinstance(value, EncryptedSecret):
            return Secret(codec.decrypt(value.ct))
        if isinstance(value, dict):
            candidate = cast(dict[object, object], value)
            if candidate.get("$enc") == ENCRYPTED_SECRET_VERSION:
                ct_raw = candidate.get("ct")
                if isinstance(ct_raw, str):
                    return Secret(codec.decrypt(ct_raw))
        if isinstance(value, str):
            return Secret(value)
        raise TypeError(f"cannot convert {type(value).__name__} to Secret")
    if target_type is object:
        if isinstance(value, EncryptedSecret):
            return Secret(codec.decrypt(value.ct))
        if isinstance(value, dict):
            candidate = cast(dict[object, object], value)
            if candidate.get("$enc") == ENCRYPTED_SECRET_VERSION:
                ct_raw = candidate.get("ct")
                if isinstance(ct_raw, str):
                    return Secret(codec.decrypt(ct_raw))
        return value
    raise TypeError(f"secret_dec_hook: unsupported target type {target_type!r}")


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
    schema: type | dict[str, object] | None,
    existing: dict[str, object] | None = None,
) -> dict[str, object]:
    """Validate an integration config schema and wrap secret fields."""

    if schema is None:
        return dict(config)
    if isinstance(schema, dict):
        return _wrap_json_schema_secrets(config, schema, existing=existing)

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


def secret_field_names(schema: type | dict[str, object] | None) -> tuple[str, ...]:
    from msgspec import Struct
    from msgspec.structs import fields

    if isinstance(schema, dict):
        return tuple(_json_schema_secret_fields(schema))
    if not isinstance(schema, type) or not issubclass(schema, Struct):
        return ()
    return tuple(field.name for field in fields(schema) if is_secret_type(field.type))


def _wrap_json_schema_secrets(
    config: dict[str, object],
    schema: dict[str, object],
    *,
    existing: dict[str, object] | None,
) -> dict[str, object]:
    wrapped = dict(config)
    previous = existing or {}
    for field_name in _json_schema_secret_fields(schema):
        if _should_keep_existing_secret(field_name, wrapped, previous):
            wrapped[field_name] = previous[field_name]
            continue
        if field_name in wrapped and not isinstance(wrapped[field_name], Secret):
            value = wrapped[field_name]
            if not isinstance(value, str):
                raise ValueError(f"secret field {field_name!r} must be a string")
            wrapped[field_name] = Secret(value)
    return wrapped


def _json_schema_secret_fields(schema: dict[str, object]) -> tuple[str, ...]:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return ()
    result: list[str] = []
    for name, field_schema in properties.items():
        if not isinstance(name, str) or not isinstance(field_schema, dict):
            continue
        schema_info = cast(dict[str, object], field_schema)
        if schema_info.get("format") == "secret":
            result.append(name)
    return tuple(result)


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


def _decode_master_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode(), validate=True)
    except binascii.Error as exc:
        raise ValueError("secrets.master_key must be base64") from exc
    if len(decoded) != 32:
        raise ValueError("secrets.master_key must decode to 32 bytes")
    return decoded
