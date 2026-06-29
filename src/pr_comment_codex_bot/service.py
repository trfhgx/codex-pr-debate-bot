from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from .codex_thread import CodexThreadClient
from .comments import (
    implementation_finished_comment,
    is_marked_bot_comment,
    mark_bot_comment,
)
from .github_client import GitHubClient
from .models import SessionState
from .security import has_trigger
from .settings import Settings
from .storage import Storage


class PRCommentService:
    def __init__(self, *, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage

    async def setup_webhook_for_repo_watch(
        self, *, watch_id: int, event_id: int | None = None
    ) -> None:
        watch = await self.storage.get_watched_repo(watch_id)
        if not watch:
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Watched repo {watch_id} does not exist",
            )
            return

        webhook_url = self._current_github_webhook_url()
        if not webhook_url:
            summary = "Cannot set up GitHub webhook because no tunnel URL is ready"
            await self.storage.update_watched_repo_webhook(
                watch_id, status="missing_tunnel", summary=summary
            )
            await self._update_event(event_id, status="blocked", summary=summary)
            return

        secret, generated = self._get_or_create_webhook_secret()
        github = GitHubClient(self.settings, installation_id=None)
        owner = str(watch["owner"])
        repo = str(watch["repo"])
        repo_full_name = str(watch["repo_full_name"])
        bootstrap_result = await self._bootstrap_bot_repo_access(
            owner=owner,
            repo=repo,
            repo_full_name=repo_full_name,
            event_id=event_id,
        )
        try:
            result = await github.ensure_repo_webhook(
                owner=owner,
                repo=repo,
                webhook_url=webhook_url,
                secret=secret,
            )
        except httpx.HTTPStatusError as exc:
            message = self._github_error_message(exc)
            await self.storage.update_watched_repo_webhook(
                watch_id, status="failed", summary=message, webhook_url=webhook_url
            )
            await self._update_event(
                event_id,
                status="failed",
                summary=message,
                details_patch={
                    "repo_full_name": repo_full_name,
                    "webhook_url": webhook_url,
                    "github_status_code": exc.response.status_code,
                    "bot_access_bootstrap": bootstrap_result,
                },
            )
            return

        action = result["action"]
        hook_id = result.get("hook_id")
        summary = f"GitHub webhook {action} for {repo_full_name}"
        await self.storage.update_watched_repo_webhook(
            watch_id,
            status=action,
            summary=summary,
            webhook_id=int(hook_id) if hook_id is not None else None,
            webhook_url=webhook_url,
        )
        await self._update_event(
            event_id,
            status=action,
            summary=summary,
            details_patch={
                "repo_full_name": repo_full_name,
                "webhook_url": webhook_url,
                "hook_id": hook_id,
                "secret_generated": generated,
                "bot_access_bootstrap": bootstrap_result,
            },
        )

    async def _bootstrap_bot_repo_access(
        self,
        *,
        owner: str,
        repo: str,
        repo_full_name: str,
        event_id: int | None,
    ) -> dict[str, Any] | None:
        bot_login = self.settings.github_bot_login
        admin_github = self._repo_admin_github_client()
        if not bot_login or admin_github is None:
            return None
        permission = self.settings.github_repo_admin_collaborator_permission
        result: dict[str, Any] = {
            "bot_login": bot_login,
            "permission": permission,
        }
        try:
            invite = await admin_github.add_repo_collaborator(
                owner=owner,
                repo=repo,
                username=bot_login,
                permission=permission,
            )
            result["invite"] = invite
        except httpx.HTTPStatusError as exc:
            result["invite"] = {
                "status": "failed",
                "github_status_code": exc.response.status_code,
                "message": self._github_api_message(exc),
            }
            await self._update_event(
                event_id,
                status="queued",
                summary=(
                    f"Could not invite {bot_login} to {repo_full_name}; "
                    "trying webhook setup with existing bot access"
                ),
                details_patch={"bot_access_bootstrap": result},
            )
            return result

        try:
            accept = await GitHubClient(
                self.settings,
                installation_id=None,
            ).accept_repository_invitation_for_repo(owner=owner, repo=repo)
            result["acceptance"] = accept
        except httpx.HTTPStatusError as exc:
            result["acceptance"] = {
                "status": "failed",
                "github_status_code": exc.response.status_code,
                "message": self._github_api_message(exc),
            }
        return result

    async def handle_pull_request_event(
        self, payload: dict[str, Any], event_id: int | None = None
    ) -> None:
        repo_full_name = (payload.get("repository") or {}).get("full_name")
        if not repo_full_name:
            await self._update_event(event_id, status="ignored", summary="Missing repo")
            return
        watch = await self.storage.get_watched_repo_by_ref(repo_full_name=repo_full_name)
        if not watch:
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Ignored PR event for unwatched repo {repo_full_name}",
            )
            return
        pr = payload.get("pull_request") or {}
        marker_text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
        if not has_trigger(marker_text, self.settings.github_trigger_phrase):
            await self._update_event(
                event_id,
                status="ignored",
                summary="Ignored PR event because marker word was not present",
                details_patch={"trigger_phrase": self.settings.github_trigger_phrase},
            )
            return
        pr_number = int(pr["number"])
        session = await self.storage.load_session(
            repo_full_name=repo_full_name, pr_number=pr_number
        )
        if session is None:
            session = SessionState(repo_full_name=repo_full_name, pr_number=pr_number)
        await self.storage.save_session(session)
        await self._update_event(
            event_id,
            status="interviewing",
            summary=(
                f"Marked {repo_full_name} PR #{pr_number} active from PR title/body marker"
            ),
            details_patch={"trigger_phrase": self.settings.github_trigger_phrase},
        )

    async def poll_watched_pr(
        self, *, watch_id: int, event_id: int | None = None
    ) -> None:
        watch = await self.storage.get_watched_pr(watch_id)
        if not watch:
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Watched PR {watch_id} does not exist",
            )
            return

        github = GitHubClient(self.settings, installation_id=None)
        owner = str(watch["owner"])
        repo = str(watch["repo"])
        pr_number = int(watch["pr_number"])
        repo_full_name = str(watch["repo_full_name"])

        pr = await github.fetch_pull_request(
            owner=owner, repo=repo, pr_number=pr_number
        )
        comments = await github.fetch_issue_comments(
            owner=owner, repo=repo, issue_number=pr_number
        )
        latest_comment_id = max((int(item["id"]) for item in comments), default=None)
        last_seen = watch.get("last_seen_comment_id")

        if last_seen is None:
            await self.storage.update_watched_pr_poll(
                watch_id,
                status="initialized",
                summary=(
                    "Initialized watch baseline"
                    if latest_comment_id
                    else "Initialized watch baseline with no comments"
                ),
                last_seen_comment_id=latest_comment_id,
            )
            await self._update_event(
                event_id,
                status="initialized",
                summary=f"Initialized watch baseline for {repo_full_name} PR #{pr_number}",
                details_patch={
                    "watched_pr": watch,
                    "comment_count": len(comments),
                    "last_seen_comment_id": latest_comment_id,
                },
            )
            return

        new_comments = [
            item for item in comments if int(item["id"]) > int(last_seen)
        ]
        processed_ids: list[int] = []
        for comment in new_comments:
            synthetic_payload = {
                "action": "created",
                "repository": {
                    "full_name": repo_full_name,
                    "name": repo,
                    "owner": {"login": owner},
                },
                "issue": {"number": pr_number, "pull_request": {"url": pr["url"]}},
                "comment": comment,
            }
            await self.handle_issue_comment(synthetic_payload)
            processed_ids.append(int(comment["id"]))

        next_seen = latest_comment_id or int(last_seen)
        summary = (
            f"Processed {len(processed_ids)} new comment(s)"
            if processed_ids
            else "No new comments"
        )
        await self.storage.update_watched_pr_poll(
            watch_id,
            status="polled",
            summary=summary,
            last_seen_comment_id=next_seen,
        )
        await self._update_event(
            event_id,
            status="polled",
            summary=f"{summary} on {repo_full_name} PR #{pr_number}",
            details_patch={
                "watched_pr": watch,
                "processed_comment_ids": processed_ids,
                "last_seen_comment_id": next_seen,
            },
        )

    async def handle_issue_comment(
        self, payload: dict[str, Any], event_id: int | None = None
    ) -> None:
        try:
            await self._handle_issue_comment(payload, event_id=event_id)
        except Exception as exc:
            if event_id is not None:
                summary = f"Processing failed: {type(exc).__name__}"
                await self.storage.update_event(
                    event_id,
                    status="failed",
                    summary=summary,
                    details_patch={"error": str(exc)},
                )
            raise

    async def _handle_issue_comment(
        self, payload: dict[str, Any], event_id: int | None = None
    ) -> None:
        if payload.get("action") not in {"created", "edited"}:
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Ignored issue_comment action {payload.get('action')}",
            )
            return
        issue = payload.get("issue") or {}
        if not issue.get("pull_request"):
            await self._update_event(
                event_id,
                status="ignored",
                summary="Ignored issue comment because it is not on a PR",
            )
            return

        comment = payload.get("comment") or {}
        comment_id = int(comment["id"])
        repo_payload = payload["repository"]
        repo_full_name = repo_payload["full_name"]
        owner, repo = repo_full_name.split("/", 1)
        pr_number = int(issue["number"])
        watch = await self.storage.get_watched_repo_by_ref(repo_full_name=repo_full_name)
        if not watch:
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Ignored PR comment for unwatched repo {repo_full_name}",
            )
            return

        if await self.storage.has_processed_comment(comment_id):
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Ignored duplicate comment {comment_id}",
            )
            return
        if self._is_own_comment(comment):
            await self.storage.mark_processed_comment(
                comment_id=comment_id,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
            )
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Ignored bot's own comment {comment_id}",
            )
            return
        if is_marked_bot_comment(comment.get("body")):
            await self.storage.mark_processed_comment(
                comment_id=comment_id,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
            )
            await self._update_event(
                event_id,
                status="ignored",
                summary=f"Ignored marked bot comment {comment_id}",
            )
            return

        session = await self.storage.load_session(
            repo_full_name=repo_full_name, pr_number=pr_number
        )
        active_session = session is not None and session.status in {
            "interviewing",
            "ready_to_implement",
            "implementing",
        }
        comment_body = comment.get("body") or ""
        if not active_session and not has_trigger(
            comment_body, self.settings.github_trigger_phrase
        ):
            await self._update_event(
                event_id,
                status="ignored",
                summary="Ignored PR comment because trigger phrase was missing",
                details_patch={"trigger_phrase": self.settings.github_trigger_phrase},
            )
            return

        await self.storage.mark_processed_comment(
            comment_id=comment_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
        )

        if session is None:
            session = SessionState(repo_full_name=repo_full_name, pr_number=pr_number)
        session.last_processed_comment_id = comment_id

        github = GitHubClient(
            self.settings, (payload.get("installation") or {}).get("id")
        )
        context = await github.fetch_pr_context(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            latest_comment=comment,
        )
        comment_style = self.settings.read_comment_style()

        debate = await CodexThreadClient(self.settings).run_debate(
            context=context,
            session=session,
            comment_style_guide=comment_style,
        )
        decision = debate.decision
        session.debate_job_id = debate.job_id or session.debate_job_id
        session.debate_thread_id = debate.thread_id or session.debate_thread_id
        session.apply_decision(decision)
        await self.storage.save_session(session)

        if decision.status in {"needs_answer", "blocked"}:
            await github.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=pr_number,
                body=mark_bot_comment(decision.reply_body),
            )
            await self._update_event(
                event_id,
                status=decision.status,
                summary="Posted interviewer reply to PR",
                details_patch={
                    "questions": [item.model_dump() for item in decision.questions],
                    "resolved_decisions": decision.resolved_decisions,
                    "unresolved_decisions": decision.unresolved_decisions,
                    "debate_job_id": session.debate_job_id,
                    "debate_thread_id": session.debate_thread_id,
                },
            )
            return

        if not decision.implementation_brief:
            await github.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=pr_number,
                body=mark_bot_comment(
                    "The plan looks ready, but I could not produce an "
                    "implementation brief. Please clarify the requested change."
                ),
            )
            await self._update_event(
                event_id,
                status="blocked",
                summary="Ready decision did not include implementation brief",
            )
            return

        await self._start_implementation(
            github=github,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            session=session,
            context=context,
            implementation_brief=decision.implementation_brief,
            comment_style=comment_style,
            event_id=event_id,
        )

    async def _start_implementation(
        self,
        *,
        github: GitHubClient,
        owner: str,
        repo: str,
        pr_number: int,
        session: SessionState,
        context: Any,
        implementation_brief: str,
        comment_style: str,
        event_id: int | None = None,
    ) -> None:
        codex_thread = CodexThreadClient(self.settings)
        result = await codex_thread.start_implementation(
            context=context,
            implementation_brief=implementation_brief,
            comment_style_guide=comment_style,
        )
        session.status = "blocked" if result.error else "implemented"
        session.codex_job_id = result.job_id
        session.codex_thread_id = result.thread_id
        session.branch = result.branch
        session.commit_sha = result.commit_sha
        await self.storage.save_session(session)
        await github.create_issue_comment(
            owner=owner,
            repo=repo,
            issue_number=pr_number,
            body=implementation_finished_comment(result),
        )
        await self._update_event(
            event_id,
            status=session.status,
            summary="Codex thread finished and final PR comment was posted",
            details_patch={"codex_thread_result": result.model_dump()},
        )

    def _is_own_comment(self, comment: dict[str, Any]) -> bool:
        login = ((comment.get("user") or {}).get("login") or "").lower()
        return bool(self.settings.github_bot_login) and (
            login == self.settings.github_bot_login.lower()
        )

    async def _update_event(
        self,
        event_id: int | None,
        *,
        status: str,
        summary: str,
        details_patch: dict[str, object] | None = None,
    ) -> None:
        if event_id is None:
            return
        await self.storage.update_event(
            event_id,
            status=status,
            summary=summary,
            details_patch=details_patch,
        )

    def _current_github_webhook_url(self) -> str | None:
        if not self.settings.tunnel_info_path.exists():
            return None
        raw = json.loads(self.settings.tunnel_info_path.read_text(encoding="utf-8"))
        public_url = str(raw.get("public_url") or "").rstrip("/")
        if not public_url:
            return None
        return f"{public_url}/webhooks/github"

    def _get_or_create_webhook_secret(self) -> tuple[str, bool]:
        if self.settings.github_webhook_secret:
            return self.settings.github_webhook_secret.get_secret_value(), False
        secret = secrets.token_urlsafe(32)
        self.settings.github_webhook_secret = SecretStr(secret)
        env_path = Path(".env")
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith("GITHUB_WEBHOOK_SECRET="):
                lines[index] = f"GITHUB_WEBHOOK_SECRET={secret}"
                replaced = True
                break
        if not replaced:
            lines.append(f"GITHUB_WEBHOOK_SECRET={secret}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return secret, True

    def _repo_admin_github_client(self) -> GitHubClient | None:
        if self.settings.github_repo_admin_token:
            return GitHubClient(
                self.settings,
                installation_id=None,
                token_override=(
                    self.settings.github_repo_admin_token.get_secret_value()
                ),
                use_settings_token=False,
            )
        if self.settings.github_repo_admin_use_gh_cli_token:
            return GitHubClient(
                self.settings,
                installation_id=None,
                use_gh_cli_token=True,
                use_settings_token=False,
            )
        return None

    @staticmethod
    def _github_error_message(exc: httpx.HTTPStatusError) -> str:
        status = exc.response.status_code
        message = PRCommentService._github_api_message(exc)
        if status in {401, 403, 404}:
            return (
                f"GitHub refused webhook setup ({status}). "
                "Confirm the bot account has admin access to the repo and hook "
                "permissions."
            )
        return f"GitHub webhook setup failed ({status}): {message}"

    @staticmethod
    def _github_api_message(exc: httpx.HTTPStatusError) -> str:
        try:
            body = exc.response.json()
        except ValueError:
            return exc.response.text
        message = body.get("message") if isinstance(body, dict) else None
        return str(message or exc.response.text)
