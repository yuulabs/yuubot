import msgspec


class IntegrationRecord(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    type: str
    name: str
    config: dict[str, object] = msgspec.field(default_factory=dict)
