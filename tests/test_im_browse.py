from __future__ import annotations

from yuubot.capabilities import CapabilityContext, execute


async def test_im_browse_accepts_numeric_qq_argument(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_browse_messages(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("yuubot.capabilities.im.browse_messages", fake_browse_messages)

    result = await execute(
        "im browse --ctx 2 --qq 326598617",
        context=CapabilityContext(ctx_id=2),
    )

    assert result[0]["text"] == "未找到消息"
    assert captured["ctx_id"] == 2
    assert captured["qq_ids"] == [326598617]
