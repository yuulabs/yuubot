import msgspec

from ..domain.stream import StreamEvent, StreamStop, StreamStopPayload, Usage


def stream_stop_event(
    reason: str,
    usage: Usage,
    account: dict[str, object],
    cost_estimated: bool,
) -> StreamEvent:
    return StreamEvent(
        "stop",
        "stream_stop",
        StreamStopPayload(
            reason,  # type: ignore[arg-type]
            usage,
            account,
            cost_estimated,
        ),
    )


def stream_stop_from(stop: StreamStop) -> StreamEvent:
    return stream_stop_event(stop.reason, stop.usage, stop.account, stop.cost_estimated)
