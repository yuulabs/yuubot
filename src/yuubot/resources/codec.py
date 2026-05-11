from __future__ import annotations

import msgspec


def encode_json(value: object) -> object:
    return msgspec.to_builtins(value)
