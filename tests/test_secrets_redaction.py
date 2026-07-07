from __future__ import annotations

from yuubot.util.secrets import REDACTED, redact_text, redact_value


def test_redact_text_masks_common_secret_patterns() -> None:
    text = "\n".join(
        [
            "api_key=sk-fake1234567890abcdef",
            "openrouter=sk-or-v1-abcdefghijklmnopqrstuvwxyz",
            "kimi=sk-kimi-abcdefghijklmnopqrstuvwxyz",
            "refresh=rt.1.ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "auth=Bearer super-secret-token-value",
        ]
    )

    redacted = redact_text(text)

    assert "sk-fake" not in redacted
    assert "sk-or-v1-" not in redacted
    assert "sk-kimi-" not in redacted
    assert "rt.1.ABCDEF" not in redacted
    assert "eyJhbGci" not in redacted
    assert "Bearer super-secret" not in redacted
    assert redacted.count(REDACTED) >= 6


def test_redact_value_redacts_secret_keys_recursively() -> None:
    payload = {
        "model": "glm-5.2-short",
        "credential": {"type": "api", "key": "sk-fake1234567890abcdef"},
        "items": ["safe", "sk-fake1234567890abcdef"],
    }

    redacted = redact_value(payload)

    assert isinstance(redacted, dict)
    assert redacted["model"] == "glm-5.2-short"
    assert redacted["credential"] == {"type": "api", "key": REDACTED}
    assert redacted["items"] == ["safe", REDACTED]
