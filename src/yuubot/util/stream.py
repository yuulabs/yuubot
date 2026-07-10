from ..domain.stream import StopReason, StreamEvent, StreamStop, StreamStopPayload, Usage


def stream_stop_event(
    reason: StopReason,
    usage: Usage,
    account: dict[str, object],
) -> StreamEvent:
    return StreamEvent(
        "stop",
        "stream_stop",
        StreamStopPayload(
            reason,
            usage,
            account,
        ),
    )


def stream_stop_from(stop: StreamStop) -> StreamEvent:
    return stream_stop_event(stop.reason, stop.usage, stop.account)
