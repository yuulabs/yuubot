import msgspec
from attrs import frozen


class TavilyWebConfig(msgspec.Struct, frozen=True, kw_only=True):
    api_key: str
    tavily_base_url: str = "https://api.tavily.com"
    timeout_s: float = 30
    user_agent: str = "yuubot/0.1"
    max_read_bytes: int = 1_048_576
    max_read_chars: int = 12_000
    max_download_bytes: int = 104_857_600


@frozen
class TavilyWebIntegration:
    name: str
    config: TavilyWebConfig
    package_path: str = "yext.web"

    def session_context(self) -> dict[str, str]:
        return {
            "YEXT_WEB_API_KEY": self.config.api_key,
            "TAVILY_BASE_URL": self.config.tavily_base_url,
            "YEXT_WEB_TIMEOUT_S": str(self.config.timeout_s),
            "YEXT_WEB_USER_AGENT": self.config.user_agent,
            "YEXT_WEB_MAX_READ_BYTES": str(self.config.max_read_bytes),
            "YEXT_WEB_MAX_READ_CHARS": str(self.config.max_read_chars),
            "YEXT_WEB_MAX_DOWNLOAD_BYTES": str(self.config.max_download_bytes),
        }

    async def close(self) -> None:
        return None


def make_tavily_web(name: str, config: msgspec.Struct, runtime: object) -> TavilyWebIntegration:
    del runtime
    return TavilyWebIntegration(name=name, config=msgspec.convert(config, TavilyWebConfig))
