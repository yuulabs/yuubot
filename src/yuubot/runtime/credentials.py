"""Encrypted credential records for daemon-managed external connections."""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import msgspec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..util.time import utc_now_iso

if TYPE_CHECKING:
    from ..db import Database

CredentialKind = Literal["oauth_token", "api_key", "manual_token"]


class CredentialRecord(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    owner_scope: str = "global"
    kind: CredentialKind
    provider: str
    label: str
    redacted_summary: str = ""
    expires_at: str | None = None
    scopes: tuple[str, ...] = ()
    secret_ref: str = ""
    created_at: str = ""
    updated_at: str = ""


class CredentialSecret(msgspec.Struct, frozen=True):
    credential_id: str
    payload: dict[str, object]


class CredentialStore:
    def __init__(self, db: Database, data_dir: Path) -> None:
        self._db = db
        self._codec = _SecretCodec(data_dir / "secrets" / "credential.key")

    async def put(
        self,
        record: CredentialRecord,
        secret_payload: dict[str, object] | None = None,
    ) -> CredentialRecord:
        now = utc_now_iso()
        stored = CredentialRecord(
            id=record.id,
            owner_scope=record.owner_scope,
            kind=record.kind,
            provider=record.provider,
            label=record.label,
            redacted_summary=record.redacted_summary,
            expires_at=record.expires_at,
            scopes=record.scopes,
            secret_ref=record.secret_ref or f"credential:{record.id}",
            created_at=record.created_at or now,
            updated_at=now,
        )
        await self._db.execute(
            """
            insert into app_credentials (id, payload, updated_at)
            values (?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (stored.id, msgspec.json.encode(stored), now),
        )
        if secret_payload is not None:
            await self._db.execute(
                """
                insert into app_credential_secrets (credential_id, encrypted_payload, updated_at)
                values (?, ?, ?)
                on conflict(credential_id) do update set
                    encrypted_payload = excluded.encrypted_payload,
                    updated_at = excluded.updated_at
                """,
                (stored.id, self._codec.encrypt(secret_payload), now),
            )
        await self._db.commit()
        return stored

    async def get(self, credential_id: str) -> CredentialRecord | None:
        cursor = await self._db.execute("select payload from app_credentials where id = ?", (credential_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return msgspec.json.decode(row[0], type=CredentialRecord)

    async def list_records(self) -> list[CredentialRecord]:
        cursor = await self._db.execute("select payload from app_credentials order by id")
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=CredentialRecord) for payload, in rows]

    async def secret_payload(self, credential_id: str) -> dict[str, object] | None:
        cursor = await self._db.execute(
            "select encrypted_payload from app_credential_secrets where credential_id = ?",
            (credential_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = self._codec.decrypt(row[0])
        return msgspec.json.decode(payload, type=dict[str, object])

    async def delete(self, credential_id: str) -> bool:
        cursor = await self._db.execute("delete from app_credentials where id = ?", (credential_id,))
        await self._db.commit()
        return cursor.rowcount > 0


class _SecretCodec:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._aead = AESGCM(self._load_or_create_key())

    def encrypt(self, payload: dict[str, object]) -> bytes:
        nonce = secrets.token_bytes(12)
        plaintext = msgspec.json.encode(payload)
        return base64.b64encode(nonce + self._aead.encrypt(nonce, plaintext, None))

    def decrypt(self, ciphertext: bytes) -> bytes:
        raw = base64.b64decode(ciphertext, validate=True)
        if len(raw) < 13:
            raise ValueError("credential ciphertext is too short")
        return self._aead.decrypt(raw[:12], raw[12:], None)

    def _load_or_create_key(self) -> bytes:
        if self._path.exists():
            return base64.b64decode(self._path.read_text(encoding="ascii").strip().encode(), validate=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        self._path.write_text(base64.b64encode(key).decode("ascii"), encoding="ascii")
        os.chmod(self._path, 0o600)
        return key
