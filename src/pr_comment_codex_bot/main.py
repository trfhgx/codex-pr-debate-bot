from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .env_file import update_env_values
from .security import verify_github_signature
from .service import PRCommentService
from .settings import Settings
from .storage import Storage

settings = Settings()
storage = Storage(settings.database_path)
service = PRCommentService(settings=settings, storage=storage)
poll_task: asyncio.Task[None] | None = None
webhook_sync_task: asyncio.Task[None] | None = None
_last_tunnel_webhook_url: str | None = None
ENV_PATH = Path(".env")


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    service.settings = settings
    return settings


def enrich_watched_repo(watch: dict[str, object]) -> dict[str, object]:
    return service.enrich_watched_repo(watch)


def set_webhook_sync_task_enabled(enabled: bool) -> None:
    global webhook_sync_task, _last_tunnel_webhook_url
    if enabled:
        if webhook_sync_task is None or webhook_sync_task.done():
            webhook_sync_task = asyncio.create_task(_tunnel_webhook_sync_loop())
    elif webhook_sync_task is not None:
        webhook_sync_task.cancel()
        webhook_sync_task = None
        _last_tunnel_webhook_url = None

app = FastAPI(title=settings.app_name)


DASHBOARD_HTML = "<div>Placeholder</div>"


@app.on_event("startup")
async def startup() -> None:
    await storage.init()
    global poll_task, webhook_sync_task
    if settings.github_poll_interval_seconds > 0:
        poll_task = asyncio.create_task(_poll_watched_prs_loop())
    set_webhook_sync_task_enabled(settings.github_auto_sync_webhooks)


@app.on_event("shutdown")
async def shutdown() -> None:
    if poll_task:
        poll_task.cancel()
    if webhook_sync_task:
        webhook_sync_task.cancel()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/settings/sync")
async def get_sync_settings() -> dict[str, object]:
    return {
        "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        "current_webhook_url": service._current_github_webhook_url(),
    }


@app.put("/settings/sync")
async def update_sync_settings(payload: dict[str, Any]) -> dict[str, object]:
    if "auto_sync_webhooks" not in payload:
        raise HTTPException(
            status_code=400, detail="auto_sync_webhooks field is required"
        )
    enabled = bool(payload["auto_sync_webhooks"])
    update_env_values(
        ENV_PATH, {"GITHUB_AUTO_SYNC_WEBHOOKS": "true" if enabled else "false"}
    )
    reload_settings()
    set_webhook_sync_task_enabled(enabled)
    if enabled:
        asyncio.create_task(service.sync_enabled_repo_webhooks(reason="always_on_enabled"))
    return {
        "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        "current_webhook_url": service._current_github_webhook_url(),
    }


@app.get("/settings/accounts")
async def get_account_settings() -> dict[str, object]:
    return settings.accounts_status()


@app.put("/settings/accounts")
async def update_account_settings(payload: dict[str, Any]) -> dict[str, object]:
    holder = payload.get("holder") or {}
    replier = payload.get("replier") or {}
    updates: dict[str, str | None] = {}

    holder_login = str(holder.get("login") or "").strip()
    replier_login = str(replier.get("login") or "").strip()
    updates["GITHUB_HOLDER_LOGIN"] = holder_login or None
    updates["GITHUB_REPLIER_LOGIN"] = replier_login or None

    holder_auth = str(holder.get("auth") or "gh_cli")
    replier_auth = str(replier.get("auth") or "gh_cli")
    updates["GITHUB_HOLDER_USE_GH_CLI_TOKEN"] = "true" if holder_auth == "gh_cli" else "false"
    updates["GITHUB_REPLIER_USE_GH_CLI_TOKEN"] = (
        "true" if replier_auth == "gh_cli" else "false"
    )

    holder_token = str(holder.get("token") or "").strip()
    replier_token = str(replier.get("token") or "").strip()
    if holder_auth == "token":
        if holder_token:
            updates["GITHUB_HOLDER_TOKEN"] = holder_token
        elif not settings.github_holder_token:
            raise HTTPException(
                status_code=400,
                detail="Holder token is required when holder auth is set to token",
            )
    else:
        updates["GITHUB_HOLDER_TOKEN"] = None

    if replier_auth == "token":
        if replier_token:
            updates["GITHUB_REPLIER_TOKEN"] = replier_token
        elif not settings.github_replier_token:
            raise HTTPException(
                status_code=400,
                detail="Replier token is required when replier auth is set to token",
            )
    else:
        updates["GITHUB_REPLIER_TOKEN"] = None

    permission = str(holder.get("collaborator_permission") or "admin").strip() or "admin"
    updates["GITHUB_HOLDER_COLLABORATOR_PERMISSION"] = permission

    update_env_values(ENV_PATH, updates)
    reload_settings()
    return settings.accounts_status()


@app.get("/tunnel-info")
async def tunnel_info() -> dict[str, object]:
    if not settings.tunnel_info_path.exists():
        return {
            "public_url": None,
            "github_webhook_url": None,
            "provider": None,
            "status": "not_started",
            "webhook_secret_configured": bool(settings.github_webhook_secret),
            "accounts": settings.accounts_status(),
            "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        }
    try:
        raw = json.loads(settings.tunnel_info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "public_url": None,
            "github_webhook_url": None,
            "provider": None,
            "status": "invalid",
            "webhook_secret_configured": bool(settings.github_webhook_secret),
            "accounts": settings.accounts_status(),
            "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        }
    public_url = str(raw.get("public_url") or "").rstrip("/")
    return {
        **raw,
        "public_url": public_url or None,
        "github_webhook_url": f"{public_url}/webhooks/github" if public_url else None,
        "webhook_secret_configured": bool(settings.github_webhook_secret),
        "accounts": settings.accounts_status(),
        "auto_sync_webhooks": settings.github_auto_sync_webhooks,
    }


@app.get("/", response_class=HTMLResponse)
async def event_dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/events")
async def events(limit: int = 50) -> list[dict[str, object]]:
    limit = max(1, min(limit, 250))
    return await storage.list_events(limit=limit)


@app.get("/events/{event_id}")
async def event_detail(event_id: int) -> dict[str, object]:
    event = await storage.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/watched-prs")
async def watched_prs() -> list[dict[str, object]]:
    return await storage.list_watched_prs()


@app.get("/watched-repos")
async def watched_repos() -> list[dict[str, object]]:
    watches = await storage.list_watched_repos()
    return [enrich_watched_repo(watch) for watch in watches]


@app.post("/watched-repos")
async def add_watched_repo(
    payload: dict[str, str], background_tasks: BackgroundTasks
) -> dict[str, object]:
    url = payload.get("url", "").strip()
    parsed = _parse_github_repo_url(url)
    watch = await storage.add_watched_repo(
        url=url,
        owner=parsed["owner"],
        repo=parsed["repo"],
    )
    event_id = await storage.record_event(
        source="dashboard",
        event_type="repo_watch_added",
        delivery_id=None,
        status="queued",
        summary=f"Added repo watch and queued webhook setup for {watch['repo_full_name']}",
        details={"watched_repo": watch},
    )
    background_tasks.add_task(
        service.setup_webhook_for_repo_watch,
        watch_id=int(watch["id"]),
        event_id=event_id,
    )
    return enrich_watched_repo(watch)


@app.patch("/watched-repos/{watch_id}")
async def update_watched_repo(
    watch_id: int,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled field is required")
    watch = await storage.get_watched_repo(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watched repo not found")

    enabled = bool(payload["enabled"])
    await storage.set_watched_repo_enabled(watch_id, enabled=enabled)
    updated = await storage.get_watched_repo(watch_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Watched repo not found")

    if enabled:
        event_id = await storage.record_event(
            source="dashboard",
            event_type="repo_watch_enabled",
            delivery_id=None,
            status="queued",
            summary=f"Enabled watch for {updated['repo_full_name']} and queued webhook setup",
            details={"watched_repo": updated},
        )
        background_tasks.add_task(
            service.setup_webhook_for_repo_watch,
            watch_id=watch_id,
            event_id=event_id,
        )
    else:
        await storage.record_event(
            source="dashboard",
            event_type="repo_watch_disabled",
            delivery_id=None,
            status="disabled",
            summary=f"Disabled watch for {updated['repo_full_name']}",
            details={"watched_repo": updated},
        )
    return enrich_watched_repo(updated)


@app.delete("/watched-repos/{watch_id}")
async def delete_watched_repo(watch_id: int) -> dict[str, str]:
    deleted = await storage.delete_watched_repo(watch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watched repo not found")
    await storage.record_event(
        source="dashboard",
        event_type="repo_watch_removed",
        delivery_id=None,
        status="removed",
        summary=f"Removed watched repo {watch_id}",
        details={"watch_id": watch_id},
    )
    return {"status": "removed"}


@app.post("/watched-repos/{watch_id}/webhook")
async def setup_watched_repo_webhook(
    watch_id: int, background_tasks: BackgroundTasks
) -> dict[str, str]:
    watch = await storage.get_watched_repo(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watched repo not found")
    event_id = await storage.record_event(
        source="dashboard",
        event_type="repo_webhook_setup",
        delivery_id=None,
        status="queued",
        summary=f"Queued webhook setup for {watch['repo_full_name']}",
        details={"watched_repo": watch},
    )
    background_tasks.add_task(
        service.setup_webhook_for_repo_watch, watch_id=watch_id, event_id=event_id
    )
    return {"status": "queued", "event_id": str(event_id)}


@app.post("/watched-prs")
async def add_watched_pr(
    payload: dict[str, str], background_tasks: BackgroundTasks
) -> dict[str, object]:
    url = payload.get("url", "").strip()
    parsed = _parse_github_pr_url(url)
    watch = await storage.add_watched_pr(
        url=url,
        owner=parsed["owner"],
        repo=parsed["repo"],
        pr_number=int(parsed["pr_number"]),
    )
    event_id = await storage.record_event(
        source="dashboard",
        event_type="watch_added",
        delivery_id=None,
        status="legacy",
        summary=(
            f"Added legacy PR watch without webhook setup for "
            f"{watch['repo_full_name']} PR #{watch['pr_number']}"
        ),
        details={"watched_pr": watch},
    )
    _ = event_id
    return watch


@app.delete("/watched-prs/{watch_id}")
async def delete_watched_pr(watch_id: int) -> dict[str, str]:
    deleted = await storage.delete_watched_pr(watch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watched PR not found")
    await storage.record_event(
        source="dashboard",
        event_type="watch_removed",
        delivery_id=None,
        status="removed",
        summary=f"Removed watched PR {watch_id}",
        details={"watch_id": watch_id},
    )
    return {"status": "removed"}


@app.post("/watched-prs/{watch_id}/poll")
async def poll_watched_pr(
    watch_id: int, background_tasks: BackgroundTasks
) -> dict[str, str]:
    watch = await storage.get_watched_pr(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watched PR not found")
    event_id = await storage.record_event(
        source="poller",
        event_type="poll_now",
        delivery_id=None,
        status="queued",
        summary=f"Queued poll for {watch['repo_full_name']} PR #{watch['pr_number']}",
        details={"watched_pr": watch},
    )
    background_tasks.add_task(service.poll_watched_pr, watch_id=watch_id, event_id=event_id)
    return {"status": "queued", "event_id": str(event_id)}


@app.post("/watched-prs/{watch_id}/webhook")
async def setup_watched_pr_webhook(
    watch_id: int, background_tasks: BackgroundTasks
) -> dict[str, str]:
    _ = (watch_id, background_tasks)
    raise HTTPException(
        status_code=410,
        detail="PR-level webhooks were removed. Add the repository under Watched Repos.",
    )


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()
    delivery_id = request.headers.get("x-github-delivery")
    secret = (
        settings.github_webhook_secret.get_secret_value()
        if settings.github_webhook_secret
        else None
    )
    if not verify_github_signature(
        body=body, signature_header=x_hub_signature_256, webhook_secret=secret
    ):
        await storage.record_event(
            source="github",
            event_type=x_github_event or "unknown",
            delivery_id=delivery_id,
            status="rejected",
            summary="Rejected GitHub webhook because signature verification failed",
            details={"headers": _safe_headers(request.headers), "body": body.decode()},
        )
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")
    payload: dict[str, Any] = await request.json()
    event_id = await storage.record_event(
        source="github",
        event_type=x_github_event or "unknown",
        delivery_id=delivery_id,
        status="received",
        summary=_github_summary(payload),
        details={"headers": _safe_headers(request.headers), "payload": payload},
    )
    if x_github_event == "issue_comment":
        background_tasks.add_task(service.handle_issue_comment, payload, event_id)
        return {"status": "queued", "event_id": str(event_id)}
    if x_github_event == "pull_request":
        background_tasks.add_task(service.handle_pull_request_event, payload, event_id)
        return {"status": "queued", "event_id": str(event_id)}
    if x_github_event not in {"issue_comment", "pull_request"}:
        await storage.update_event(
            event_id,
            status="ignored",
            summary=f"Ignored unsupported GitHub event {x_github_event}",
        )
        return {"status": "ignored", "event_id": str(event_id)}
    return {"status": "ignored", "event_id": str(event_id)}


@app.get("/debug/sessions")
async def debug_sessions() -> list[dict[str, object]]:
    return await storage.debug_dump()


def _safe_headers(headers: Any) -> dict[str, str]:
    redacted = {"authorization", "x-hub-signature-256"}
    return {
        key: ("<redacted>" if key.lower() in redacted else value)
        for key, value in dict(headers).items()
    }


def _github_summary(payload: dict[str, Any]) -> str:
    action = payload.get("action", "unknown")
    repo = (payload.get("repository") or {}).get("full_name", "unknown repo")
    pr = payload.get("pull_request") or {}
    if pr:
        return f"GitHub {action} pull_request on {repo} PR #{pr.get('number', '?')}"
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    user = (comment.get("user") or {}).get("login", "unknown user")
    issue_number = issue.get("number", "?")
    return f"GitHub {action} comment from {user} on {repo} PR #{issue_number}"


def _parse_github_pr_url(url: str) -> dict[str, object]:
    match = re.match(
        r"^https://github\.com/([^/\s]+)/([^/\s]+)/pull/([0-9]+)(?:[/?#].*)?$",
        url,
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Expected a GitHub PR URL like https://github.com/owner/repo/pull/123",
        )
    owner, repo, pr_number = match.groups()
    return {"owner": owner, "repo": repo, "pr_number": int(pr_number)}


def _parse_github_repo_url(url: str) -> dict[str, str]:
    match = re.match(
        r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?(?:[?#].*)?$",
        url,
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Expected a GitHub repo URL like https://github.com/owner/repo",
        )
    owner, repo = match.groups()
    return {"owner": owner, "repo": repo}


async def _tunnel_webhook_sync_loop() -> None:
    global _last_tunnel_webhook_url
    tick = 0
    while True:
        try:
            if settings.github_auto_sync_webhooks:
                webhook_url = service._current_github_webhook_url()
                if webhook_url:
                    url_changed = webhook_url != _last_tunnel_webhook_url
                    if url_changed:
                        _last_tunnel_webhook_url = webhook_url
                        await service.sync_enabled_repo_webhooks(
                            reason="tunnel_url_changed"
                        )
                    elif tick % 6 == 0:
                        await service.sync_enabled_repo_webhooks(reason="retry")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await storage.record_event(
                source="sync",
                event_type="repo_webhook_auto_sync",
                delivery_id=None,
                status="failed",
                summary=f"Automatic webhook sync failed: {type(exc).__name__}",
                details={"error": str(exc)},
            )
        tick += 1
        await asyncio.sleep(5)


async def _poll_watched_prs_loop() -> None:
    while True:
        await asyncio.sleep(settings.github_poll_interval_seconds)
        for watch in await storage.list_watched_prs():
            if not watch.get("enabled"):
                continue
            event_id = await storage.record_event(
                source="poller",
                event_type="scheduled_poll",
                delivery_id=None,
                status="queued",
                summary=(
                    f"Scheduled poll for {watch['repo_full_name']} "
                    f"PR #{watch['pr_number']}"
                ),
                details={"watched_pr": watch},
            )
            try:
                await service.poll_watched_pr(watch_id=int(watch["id"]), event_id=event_id)
            except Exception as exc:
                await storage.update_event(
                    event_id,
                    status="failed",
                    summary=f"Scheduled poll failed: {type(exc).__name__}",
                    details_patch={"error": str(exc)},
                )
