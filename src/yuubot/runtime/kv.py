"""Actor-scoped JSON document store backed by data_dir/kv/{actor_id}/."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import msgspec
from attrs import define

from ..util.time import utc_now_iso


class JsonDocument(msgspec.Struct, frozen=True):
    actor_id: str
    key: str
    value: object
    updated_at: str
    etag: str


class KvError(Exception):
    pass


class KvBadRequestError(KvError):
    pass


class KvConflictError(KvError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class KvPutBody(msgspec.Struct, frozen=True):
    value: object


_MAX_BYTES = 1_048_576


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def compute_etag(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def normalize_key(key: str) -> str:
    if "\\" in key:
        raise KvBadRequestError("key must use forward slashes")
    if key.startswith("/"):
        raise KvBadRequestError("key must be a relative path")
    parts = [part for part in key.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise KvBadRequestError("key must not contain ..")
    if not parts:
        raise KvBadRequestError("key is required")
    return "/".join(parts)


def parse_if_match(header: str | None) -> str | None:
    if header is None:
        return None
    value = header.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def document_path(actor_root: Path, key: str) -> Path:
    return actor_root.joinpath(*key.split("/")).with_suffix(".json")


def document_snapshot(document: JsonDocument) -> dict[str, object]:
    return msgspec.to_builtins(document)  # type: ignore[return-value]

def _assert_json_value(value: object) -> None:
    if not isinstance(value, (dict, list)):
        raise KvBadRequestError("value must be a JSON object or array")


def _assert_json_size(value: object) -> None:
    if len(canonical_json(value).encode()) > _MAX_BYTES:
        raise KvBadRequestError("value exceeds maximum size of 1 MiB")


def _assert_no_prefix_collision(actor_root: Path, key: str) -> None:
    parts = key.split("/")
    if len(parts) > 1:
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if document_path(actor_root, ancestor).is_file():
                raise KvConflictError(
                    "key collides with an existing document prefix",
                    reason="key_collides_with_prefix",
                )
    if len(parts) == 1 and (actor_root / parts[0]).is_dir():
        raise KvConflictError(
            "key collides with an existing document prefix",
            reason="key_collides_with_prefix",
        )


def _read_file(path: Path) -> tuple[object, str]:
    stored = msgspec.json.decode(path.read_bytes())
    if not isinstance(stored, dict):
        raise KvError(f"invalid kv document: {path}")
    value = stored["value"]
    updated_at = stored["updated_at"]
    if not isinstance(updated_at, str):
        raise KvError(f"invalid kv document timestamp: {path}")
    return value, updated_at


def _write_atomic(path: Path, value: object, updated_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_bytes(msgspec.json.encode({"value": value, "updated_at": updated_at}))
    os.replace(tmp, path)


@define
class KvStore:
    data_dir: Path

    @property
    def root(self) -> Path:
        return self.data_dir / "kv"

    def actor_root(self, actor_id: str) -> Path:
        return self.root / actor_id

    async def get(self, actor_id: str, key: str) -> JsonDocument | None:
        normalized = normalize_key(key)
        path = document_path(self.actor_root(actor_id), normalized)
        if not path.is_file():
            return None
        value, updated_at = _read_file(path)
        return JsonDocument(
            actor_id=actor_id,
            key=normalized,
            value=value,
            updated_at=updated_at,
            etag=compute_etag(value),
        )

    async def put(
        self,
        actor_id: str,
        key: str,
        value: object,
        *,
        if_match: str | None = None,
    ) -> JsonDocument:
        normalized = normalize_key(key)
        _assert_json_value(value)
        _assert_json_size(value)
        actor_root = self.actor_root(actor_id)
        path = document_path(actor_root, normalized)
        _assert_no_prefix_collision(actor_root, normalized)
        if if_match is not None:
            if not path.is_file():
                raise KvConflictError("etag does not match", reason="etag_mismatch")
            current_value, _ = _read_file(path)
            if compute_etag(current_value) != if_match:
                raise KvConflictError("etag does not match", reason="etag_mismatch")
        updated_at = utc_now_iso(zulu=True)
        _write_atomic(path, value, updated_at)
        return JsonDocument(
            actor_id=actor_id,
            key=normalized,
            value=value,
            updated_at=updated_at,
            etag=compute_etag(value),
        )

    async def delete(self, actor_id: str, key: str) -> bool:
        normalized = normalize_key(key)
        path = document_path(self.actor_root(actor_id), normalized)
        if not path.is_file():
            return False
        path.unlink()
        return True
