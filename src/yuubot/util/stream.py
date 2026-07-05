import msgspec

from ..domain.stream import StreamEvent, StreamStop, Usage


def stream_stop_event(
    reason: str,
    usage: Usage,
    account: dict[str, object],
    *,
    cost_estimated: bool,
) -> StreamEvent:
    return StreamEvent(
        group_id="stop",
        kind="stream_stop",
        payload={
            "reason": reason,
            "usage": msgspec.to_builtins(usage),
            "account": account,
            "cost_estimated": cost_estimated,
        },
    )


def stream_stop_from(stop: StreamStop) -> StreamEvent:
    return stream_stop_event(stop.reason, stop.usage, stop.account, cost_estimated=stop.cost_estimated)
