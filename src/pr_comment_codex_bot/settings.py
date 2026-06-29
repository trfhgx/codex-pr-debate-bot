from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "pr-comment-codex-bot"
    database_path: Path = Path("./bot.sqlite3")
    tunnel_info_path: Path = Path("./tunnel-info.json")

    github_api_url: str = "https://api.github.com"
    github_webhook_secret: SecretStr | None = None
    github_app_id: str | None = None
    github_private_key: SecretStr | None = None
    github_private_key_path: Path | None = None
    github_trigger_phrase: str = "codex"
    github_poll_interval_seconds: float = 0
    github_auto_sync_webhooks: bool = True

    github_holder_login: str | None = None
    github_holder_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GITHUB_HOLDER_TOKEN", "GITHUB_REPO_ADMIN_TOKEN"
        ),
    )
    github_holder_use_gh_cli_token: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "GITHUB_HOLDER_USE_GH_CLI_TOKEN", "GITHUB_REPO_ADMIN_USE_GH_CLI_TOKEN"
        ),
    )
    github_holder_collaborator_permission: str = Field(
        default="admin",
        validation_alias=AliasChoices(
            "GITHUB_HOLDER_COLLABORATOR_PERMISSION",
            "GITHUB_REPO_ADMIN_COLLABORATOR_PERMISSION",
        ),
    )

    github_replier_login: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_REPLIER_LOGIN", "GITHUB_BOT_LOGIN"),
    )
    github_replier_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_REPLIER_TOKEN", "GITHUB_TOKEN"),
    )
    github_replier_use_gh_cli_token: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "GITHUB_REPLIER_USE_GH_CLI_TOKEN", "GITHUB_USE_GH_CLI_TOKEN"
        ),
    )

    comment_style_path: Path = Path("docs/comment-style.md")

    codex_thread_ws_url: str = "ws://127.0.0.1:8765"
    codex_thread_cwd: Path = Path("/tmp")
    codex_thread_model: str | None = None
    codex_thread_effort: str = "medium"
    codex_thread_timeout_seconds: float = 600.0

    @field_validator(
        "github_holder_token",
        "github_replier_token",
        "github_webhook_secret",
        "github_private_key",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        if value == "" or value is None:
            return None
        return value

    def read_comment_style(self) -> str:
        if not self.comment_style_path.exists():
            return ""
        return self.comment_style_path.read_text(encoding="utf-8")

    def read_github_private_key(self) -> str | None:
        if self.github_private_key:
            return self.github_private_key.get_secret_value().replace("\\n", "\n")
        if self.github_private_key_path and self.github_private_key_path.exists():
            return self.github_private_key_path.read_text(encoding="utf-8")
        return None

    def holder_auth_mode(self) -> str:
        if self.github_holder_token:
            return "token"
        if self.github_holder_use_gh_cli_token:
            return "gh_cli"
        return "missing"

    def replier_auth_mode(self) -> str:
        if self.github_replier_token:
            return "token"
        if self.github_replier_use_gh_cli_token:
            return "gh_cli"
        if self.github_app_id and self.read_github_private_key():
            return "github_app"
        return "missing"

    def accounts_status(self) -> dict[str, object]:
        return {
            "holder": {
                "login": self.github_holder_login,
                "auth": self.holder_auth_mode(),
                "token_configured": bool(self.github_holder_token),
                "use_gh_cli_token": self.github_holder_use_gh_cli_token,
                "collaborator_permission": self.github_holder_collaborator_permission,
            },
            "replier": {
                "login": self.github_replier_login,
                "auth": self.replier_auth_mode(),
                "token_configured": bool(self.github_replier_token),
                "use_gh_cli_token": self.github_replier_use_gh_cli_token,
            },
        }