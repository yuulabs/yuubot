import msgspec

from ...runtime.cron.models import CronAction, CronSchedule
from ...runtime.tasks import TaskDelivery
from ...runtime.mcp import McpAuthMode, McpTransport
from ...runtime.auth_attempts import AuthAttemptStatus


class McpServerBody(msgspec.Struct, frozen=True):
    name: str
    endpoint_url: str
    transport: McpTransport = "http"
    auth_mode: McpAuthMode = "none"
    enabled: bool = True
    api_key: str = ""
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    oauth_issuer: str = ""
    oauth_authorization_endpoint: str = ""
    oauth_token_endpoint: str = ""
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_scope: str = ""


class McpReadResourceBody(msgspec.Struct, frozen=True):
    uri: str


class AuthAttemptUpdateBody(msgspec.Struct, frozen=True):
    status: AuthAttemptStatus
    error: str | None = None
    action: dict[str, object] | None = None


class SubmitTaskBody(msgspec.Struct, frozen=True):
    name: str
    shell: str
    intro: str
    owner: str
    delivery: TaskDelivery
    wait_s: float = 20
    ttl_s: float | None = None


class TaskStdinBody(msgspec.Struct, frozen=True):
    text: str


class PublishShareBody(msgspec.Struct, frozen=True):
    actor_id: str
    source_path: str
    expires_at: str | None = None


class WorkspaceDeleteBody(msgspec.Struct, frozen=True):
    paths: list[str]


class WorkspaceRenameBody(msgspec.Struct, frozen=True):
    path: str
    name: str


class WorkspaceMoveBody(msgspec.Struct, frozen=True):
    sources: list[str]
    destination: str


class WorkspaceMkdirBody(msgspec.Struct, frozen=True):
    path: str


class CreateCronJobBody(msgspec.Struct, frozen=True):
    name: str
    owner: str
    schedule: CronSchedule
    action: CronAction
    once: bool = False


class PushSubscriptionBody(msgspec.Struct, frozen=True):
    endpoint: str
    keys: dict[str, str]
