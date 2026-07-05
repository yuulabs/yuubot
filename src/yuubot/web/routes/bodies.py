import msgspec


class SubmitTaskBody(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    shell: str
    intro: str
    owner: str
    wait_s: float = 20


class PublishShareBody(msgspec.Struct, frozen=True, kw_only=True):
    actor_id: str
    source_path: str
    expires_at: str | None = None


class WorkspaceDeleteBody(msgspec.Struct, frozen=True, kw_only=True):
    paths: list[str]


class WorkspaceRenameBody(msgspec.Struct, frozen=True, kw_only=True):
    path: str
    name: str


class WorkspaceMoveBody(msgspec.Struct, frozen=True, kw_only=True):
    sources: list[str]
    destination: str


class WorkspaceMkdirBody(msgspec.Struct, frozen=True, kw_only=True):
    path: str


class CreateCronJobBody(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    owner: str
    schedule: dict[str, object]
    action: dict[str, object]
    once: bool = False


class PushSubscriptionBody(msgspec.Struct, frozen=True, kw_only=True):
    endpoint: str
    keys: dict[str, str]
