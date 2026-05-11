"""Secret codec.

Secret handling is configured by BootstrapConfig and never leaked through core
views.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


class SecretCodec:
    def __init__(self, master_key: str) -> None:
        self._master_key = master_key.encode()

    def encrypt(self, plaintext: str) -> str:
        body = base64.urlsafe_b64encode(plaintext.encode()).decode()
        signature = hmac.new(
            self._master_key, body.encode(), hashlib.sha256
        ).hexdigest()
        return f"v2-placeholder:{signature}:{body}"

    def decrypt(self, ciphertext: str) -> str:
        prefix, signature, body = ciphertext.split(":", 2)
        if prefix != "v2-placeholder":
            raise ValueError("unsupported secret ciphertext version")
        expected = hmac.new(self._master_key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("secret signature mismatch")
        return base64.urlsafe_b64decode(body.encode()).decode()

    @staticmethod
    def mask(ciphertext: str) -> str:
        return f"set:{len(ciphertext)}"
