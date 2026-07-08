import msgspec
from attrs import frozen


class GitHubConfig(msgspec.Struct, frozen=True):
    access_token: str
    default_owner: str = ""
    default_repo: str = ""
    base_url: str = "https://api.github.com"


@frozen
class GitHubIntegration:
    name: str
    config: GitHubConfig
    package_path: str = "yext.github"

    def session_context(self) -> dict[str, str]:
        return {
            "YEXT_GITHUB_ACCESS_TOKEN": self.config.access_token,
            "YEXT_GITHUB_DEFAULT_OWNER": self.config.default_owner,
            "YEXT_GITHUB_DEFAULT_REPO": self.config.default_repo,
            "YEXT_GITHUB_BASE_URL": self.config.base_url,
        }

    async def close(self) -> None:
        return None


def make_github(name: str, config: msgspec.Struct, runtime: object) -> GitHubIntegration:
    del runtime
    return GitHubIntegration(name, msgspec.convert(config, GitHubConfig))
