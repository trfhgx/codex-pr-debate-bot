from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import jwt

from .models import PullRequestContext, PullRequestRef, RepoRef
from .settings import Settings


class GitHubClient:
    def __init__(
        self,
        settings: Settings,
        installation_id: int | None,
        *,
        token_override: str | None = None,
        use_gh_cli_token: bool | None = None,
        use_settings_token: bool = True,
    ) -> None:
        self.settings = settings
        self.installation_id = installation_id
        self.token_override = token_override
        self.use_gh_cli_token = use_gh_cli_token
        self.use_settings_token = use_settings_token
        self._token: str | None = None

    async def fetch_pr_context(
        self, *, owner: str, repo: str, pr_number: int, latest_comment: dict[str, Any]
    ) -> PullRequestContext:
        pr = await self._request_json("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")
        files = await self._paginate(f"/repos/{owner}/{repo}/pulls/{pr_number}/files")
        issue_comments = await self._paginate(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments"
        )
        review_comments = await self._paginate(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        )
        reviews = await self._paginate(f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews")
        commits = await self._paginate(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/commits"
        )
        diff = await self._request_text(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )

        return PullRequestContext(
            repo=RepoRef(owner=owner, name=repo, full_name=f"{owner}/{repo}"),
            pr=PullRequestRef(
                number=pr["number"],
                title=pr["title"],
                body=pr.get("body"),
                base_ref=pr["base"]["ref"],
                head_ref=pr["head"]["ref"],
                head_sha=pr["head"]["sha"],
                clone_url=pr["head"]["repo"]["clone_url"],
                html_url=pr["html_url"],
            ),
            latest_comment=latest_comment,
            issue_comments=issue_comments,
            review_comments=review_comments,
            reviews=reviews,
            commits=commits,
            files=files,
            diff=diff,
        )

    async def fetch_pull_request(
        self, *, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        return await self._request_json("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")

    async def fetch_issue_comments(
        self, *, owner: str, repo: str, issue_number: int
    ) -> list[dict[str, Any]]:
        return await self._paginate(f"/repos/{owner}/{repo}/issues/{issue_number}/comments")

    async def fetch_file(
        self, *, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str:
        params = {"ref": ref} if ref else None
        return await self._request_text(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params,
            headers={"Accept": "application/vnd.github.raw"},
        )

    async def create_issue_comment(
        self, *, owner: str, repo: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )

    async def ensure_repo_webhook(
        self, *, owner: str, repo: str, webhook_url: str, secret: str
    ) -> dict[str, Any]:
        hooks = await self._paginate(f"/repos/{owner}/{repo}/hooks")
        payload = {
            "name": "web",
            "active": True,
            "events": ["issue_comment", "pull_request"],
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "secret": secret,
                "insecure_ssl": "0",
            },
        }
        bot_hooks = [hook for hook in hooks if self._looks_like_bot_hook(hook)]
        exact_hooks = [
            hook for hook in bot_hooks if (hook.get("config") or {}).get("url") == webhook_url
        ]
        candidates = exact_hooks + [
            hook for hook in bot_hooks if hook not in exact_hooks
        ]

        if candidates:
            updated = await self._request_json(
                "PATCH",
                f"/repos/{owner}/{repo}/hooks/{candidates[0]['id']}",
                json=payload,
            )
            for duplicate in candidates[1:]:
                await self._request_empty(
                    "DELETE", f"/repos/{owner}/{repo}/hooks/{duplicate['id']}"
                )
            return {
                "action": "updated",
                "hook_id": updated.get("id"),
                "webhook_url": webhook_url,
                "events": updated.get("events", []),
                "deleted_duplicate_hook_ids": [
                    item.get("id") for item in candidates[1:]
                ],
            }
        created = await self._request_json(
            "POST", f"/repos/{owner}/{repo}/hooks", json=payload
        )
        return {
            "action": "created",
            "hook_id": created.get("id"),
            "webhook_url": webhook_url,
            "events": created.get("events", []),
        }

    async def add_repo_collaborator(
        self, *, owner: str, repo: str, username: str, permission: str
    ) -> dict[str, Any]:
        response = await self._request_json_or_empty(
            "PUT",
            f"/repos/{owner}/{repo}/collaborators/{username}",
            json={"permission": permission},
        )
        if response is None:
            return {"status": "already_collaborator"}
        return {"status": "invited", "invitation": response}

    async def accept_repository_invitation_for_repo(
        self, *, owner: str, repo: str
    ) -> dict[str, Any]:
        repo_full_name = f"{owner}/{repo}".lower()
        invitations = await self._paginate("/user/repository_invitations")
        for invitation in invitations:
            invitation_repo = invitation.get("repository") or {}
            if str(invitation_repo.get("full_name") or "").lower() != repo_full_name:
                continue
            invitation_id = invitation["id"]
            await self._request_empty(
                "PATCH", f"/user/repository_invitations/{invitation_id}"
            )
            return {"status": "accepted", "invitation_id": invitation_id}
        return {"status": "no_pending_invitation"}

    @staticmethod
    def _looks_like_bot_hook(hook: dict[str, Any]) -> bool:
        config = hook.get("config") or {}
        url = str(config.get("url") or "")
        events = set(hook.get("events") or [])
        return (
            hook.get("name") == "web"
            and url.endswith("/webhooks/github")
            and {"issue_comment", "pull_request"}.issubset(events)
        )

    async def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        token = await self._get_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra:
            headers.update(extra)
        return headers

    async def _get_token(self) -> str:
        if self.token_override:
            return self.token_override
        if self.use_settings_token and self.settings.github_token:
            return self.settings.github_token.get_secret_value()
        if self._token:
            return self._token
        use_gh_cli_token = (
            self.settings.github_use_gh_cli_token
            if self.use_gh_cli_token is None
            else self.use_gh_cli_token
        )
        if use_gh_cli_token:
            token = await self._get_gh_cli_token()
            if token:
                self._token = token
                return token
        if not self.installation_id:
            raise RuntimeError(
                "Missing GitHub auth. Set GITHUB_TOKEN, configure a GitHub App, "
                "or login with gh CLI."
            )
        app_jwt = self._make_app_jwt()
        url = (
            f"{self.settings.github_api_url}"
            f"/app/installations/{self.installation_id}/access_tokens"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {app_jwt}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        response.raise_for_status()
        self._token = response.json()["token"]
        return self._token

    async def _get_gh_cli_token(self) -> str | None:
        env = os.environ.copy()
        if not self.use_settings_token:
            env.pop("GITHUB_TOKEN", None)
            env.pop("GH_TOKEN", None)
        process = await asyncio.create_subprocess_exec(
            "gh",
            "auth",
            "token",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None
        token = stdout.decode().strip()
        return token or None

    def _make_app_jwt(self) -> str:
        private_key = self.settings.read_github_private_key()
        if not self.settings.github_app_id or not private_key:
            raise RuntimeError("GitHub App auth requires app id and private key")
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 9 * 60,
            "iss": self.settings.github_app_id,
        }
        return jwt.encode(payload, private_key, algorithm="RS256")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.settings.github_api_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                url,
                json=json,
                params=params,
                headers=await self._headers(headers),
            )
        response.raise_for_status()
        return response.json()

    async def _request_json_or_empty(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any | None:
        url = f"{self.settings.github_api_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                url,
                json=json,
                params=params,
                headers=await self._headers(headers),
            )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def _request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        url = f"{self.settings.github_api_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                url,
                params=params,
                headers=await self._headers(headers),
            )
        response.raise_for_status()
        return response.text

    async def _request_empty(self, method: str, path: str) -> None:
        url = f"{self.settings.github_api_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url, headers=await self._headers())
        response.raise_for_status()

    async def _paginate(self, path: str) -> list[dict[str, Any]]:
        page = 1
        results: list[dict[str, Any]] = []
        while True:
            batch = await self._request_json(
                "GET", path, params={"per_page": 100, "page": page}
            )
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results
