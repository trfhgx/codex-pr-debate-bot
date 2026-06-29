from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
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
    github_token: SecretStr | None = None
    github_use_gh_cli_token: bool = True
    github_bot_login: str | None = None
    github_repo_admin_token: SecretStr | None = None
    github_repo_admin_use_gh_cli_token: bool = False
    github_repo_admin_collaborator_permission: str = "admin"
    github_trigger_phrase: str = "codex"
    github_poll_interval_seconds: float = 0

    comment_style_path: Path = Path("docs/comment-style.md")

    codex_thread_ws_url: str = "ws://127.0.0.1:8765"
    codex_thread_cwd: Path = Path("/tmp")
    codex_thread_model: str | None = None
    codex_thread_effort: str = "medium"
    codex_thread_timeout_seconds: float = 600.0

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
