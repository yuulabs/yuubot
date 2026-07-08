import msgspec


class IntegrationRecord(msgspec.Struct, frozen=True):
    id: str
    type: str
    name: str
    config: dict[str, object] = msgspec.field(default_factory=dict)


class IntegrationConfigInput(msgspec.Struct, frozen=True):
    name: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
