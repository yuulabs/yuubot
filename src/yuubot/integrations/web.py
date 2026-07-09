import msgspec
from attrs import frozen


class WebConfig(msgspec.Struct, frozen=True):
    tavily_api_key: str
    jina_api_key: str = ""
    read_backends: list[str] = msgspec.field(default_factory=lambda: ["jina", "tavily", "httpx"])
    tavily_base_url: str = "https://api.tavily.com"
    jina_base_url: str = "https://r.jina.ai"
    timeout_s: float = 30
    jina_timeout_s: float = 30
    user_agent: str = "yuubot/0.1"
    max_read_bytes: int = 1_048_576
    max_read_chars: int = 12_000
    max_download_bytes: int = 104_857_600
    tavily_extract_depth: str = "basic"
    tavily_extract_format: str = "markdown"


@frozen
class WebIntegration:
    name: str
    config: WebConfig
    package_path: str = "yext.web"

    def session_context(self) -> dict[str, str]:
        return {
            "YEXT_WEB_TAVILY_API_KEY": self.config.tavily_api_key,
            "TAVILY_BASE_URL": self.config.tavily_base_url,
            "YEXT_WEB_JINA_API_KEY": self.config.jina_api_key,
            "YEXT_WEB_JINA_BASE_URL": self.config.jina_base_url,
            "YEXT_WEB_TIMEOUT_S": str(self.config.timeout_s),
            "YEXT_WEB_JINA_TIMEOUT_S": str(self.config.jina_timeout_s),
            "YEXT_WEB_USER_AGENT": self.config.user_agent,
            "YEXT_WEB_READ_BACKENDS": ",".join(self.config.read_backends),
            "YEXT_WEB_MAX_READ_BYTES": str(self.config.max_read_bytes),
            "YEXT_WEB_MAX_READ_CHARS": str(self.config.max_read_chars),
            "YEXT_WEB_MAX_DOWNLOAD_BYTES": str(self.config.max_download_bytes),
            "YEXT_WEB_TAVILY_EXTRACT_DEPTH": self.config.tavily_extract_depth,
            "YEXT_WEB_TAVILY_EXTRACT_FORMAT": self.config.tavily_extract_format,
        }

    async def close(self) -> None:
        return None


def make_web(name: str, config: msgspec.Struct, runtime: object) -> WebIntegration:
    del runtime
    return WebIntegration(name, msgspec.convert(config, WebConfig))
