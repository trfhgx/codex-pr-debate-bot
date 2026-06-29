from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .models import SessionState


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                create table if not exists pr_sessions (
                    repo_full_name text not null,
                    pr_number integer not null,
                    state_json text not null,
                    updated_at text not null,
                    primary key (repo_full_name, pr_number)
                )
                """
            )
            await db.execute(
                """
                create table if not exists processed_comments (
                    comment_id integer primary key,
                    repo_full_name text not null,
                    pr_number integer not null,
                    processed_at text not null
                )
                """
            )
            await db.execute(
                """
                create table if not exists webhook_events (
                    id integer primary key autoincrement,
                    received_at text not null,
                    source text not null,
                    event_type text not null,
                    delivery_id text,
                    status text not null,
                    summary text not null,
                    details_json text not null
                )
                """
            )
            await db.execute(
                """
                create table if not exists watched_prs (
                    id integer primary key autoincrement,
                    url text not null unique,
                    owner text not null,
                    repo text not null,
                    repo_full_name text not null,
                    pr_number integer not null,
                    enabled integer not null default 1,
                    created_at text not null,
                    last_polled_at text,
                    last_poll_status text,
                    last_poll_summary text,
                    last_seen_comment_id integer,
                    last_webhook_status text,
                    last_webhook_summary text,
                    webhook_id integer,
                    webhook_url text,
                    unique(repo_full_name, pr_number)
                )
                """
            )
            await db.execute(
                """
                create table if not exists watched_repos (
                    id integer primary key autoincrement,
                    url text not null unique,
                    owner text not null,
                    repo text not null,
                    repo_full_name text not null unique,
                    enabled integer not null default 1,
                    created_at text not null,
                    last_webhook_status text,
                    last_webhook_summary text,
                    webhook_id integer,
                    webhook_url text
                )
                """
            )
            await self._ensure_column(db, "watched_prs", "last_webhook_status", "text")
            await self._ensure_column(db, "watched_prs", "last_webhook_summary", "text")
            await self._ensure_column(db, "watched_prs", "webhook_id", "integer")
            await self._ensure_column(db, "watched_prs", "webhook_url", "text")
            await db.commit()

    async def add_watched_repo(
        self, *, url: str, owner: str, repo: str
    ) -> dict[str, object]:
        now = datetime.now(timezone.utc).isoformat()
        repo_full_name = f"{owner}/{repo}"
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                insert into watched_repos (
                    url, owner, repo, repo_full_name, enabled, created_at
                )
                values (?, ?, ?, ?, 1, ?)
                on conflict(repo_full_name) do update set
                    url = excluded.url,
                    enabled = 1
                """,
                (url, owner, repo, repo_full_name, now),
            )
            await db.commit()
        watch = await self.get_watched_repo_by_ref(repo_full_name=repo_full_name)
        if not watch:
            raise RuntimeError("Failed to save watched repo")
        return watch

    async def list_watched_repos(self) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, url, owner, repo, repo_full_name, enabled, created_at,
                    last_webhook_status, last_webhook_summary, webhook_id, webhook_url
                from watched_repos
                order by id desc
                """
            )
        return [self._watched_repo_from_row(row) for row in rows]

    async def get_watched_repo(self, watch_id: int) -> dict[str, object] | None:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, url, owner, repo, repo_full_name, enabled, created_at,
                    last_webhook_status, last_webhook_summary, webhook_id, webhook_url
                from watched_repos
                where id = ?
                """,
                (watch_id,),
            )
        if not rows:
            return None
        return self._watched_repo_from_row(rows[0])

    async def get_watched_repo_by_ref(
        self, *, repo_full_name: str
    ) -> dict[str, object] | None:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, url, owner, repo, repo_full_name, enabled, created_at,
                    last_webhook_status, last_webhook_summary, webhook_id, webhook_url
                from watched_repos
                where repo_full_name = ?
                """,
                (repo_full_name,),
            )
        if not rows:
            return None
        return self._watched_repo_from_row(rows[0])

    async def delete_watched_repo(self, watch_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "delete from watched_repos where id = ?", (watch_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_watched_repo_webhook(
        self,
        watch_id: int,
        *,
        status: str,
        summary: str,
        webhook_id: int | None = None,
        webhook_url: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                update watched_repos
                set last_webhook_status = ?, last_webhook_summary = ?,
                    webhook_id = coalesce(?, webhook_id),
                    webhook_url = coalesce(?, webhook_url)
                where id = ?
                """,
                (status, summary, webhook_id, webhook_url, watch_id),
            )
            await db.commit()

    async def record_event(
        self,
        *,
        source: str,
        event_type: str,
        delivery_id: str | None,
        status: str,
        summary: str,
        details: dict[str, object],
    ) -> int:
        received_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                insert into webhook_events (
                    received_at, source, event_type, delivery_id, status,
                    summary, details_json
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    received_at,
                    source,
                    event_type,
                    delivery_id,
                    status,
                    summary,
                    json.dumps(details, default=str),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def update_event(
        self,
        event_id: int,
        *,
        status: str | None = None,
        summary: str | None = None,
        details_patch: dict[str, object] | None = None,
    ) -> None:
        event = await self.get_event(event_id)
        if not event:
            return
        details = dict(event["details"])
        if details_patch:
            details.update(details_patch)
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                update webhook_events
                set status = ?, summary = ?, details_json = ?
                where id = ?
                """,
                (
                    status or str(event["status"]),
                    summary or str(event["summary"]),
                    json.dumps(details, default=str),
                    event_id,
                ),
            )
            await db.commit()

    async def list_events(self, limit: int = 50) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, received_at, source, event_type, delivery_id, status,
                    summary, details_json
                from webhook_events
                order by id desc
                limit ?
                """,
                (limit,),
            )
        return [self._event_from_row(row) for row in rows]

    async def get_event(self, event_id: int) -> dict[str, object] | None:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, received_at, source, event_type, delivery_id, status,
                    summary, details_json
                from webhook_events
                where id = ?
                """,
                (event_id,),
            )
        if not rows:
            return None
        return self._event_from_row(rows[0])

    async def has_processed_comment(self, comment_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            row = await db.execute_fetchall(
                "select 1 from processed_comments where comment_id = ?",
                (comment_id,),
            )
        return bool(row)

    async def add_watched_pr(
        self, *, url: str, owner: str, repo: str, pr_number: int
    ) -> dict[str, object]:
        now = datetime.now(timezone.utc).isoformat()
        repo_full_name = f"{owner}/{repo}"
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                insert into watched_prs (
                    url, owner, repo, repo_full_name, pr_number, enabled, created_at
                )
                values (?, ?, ?, ?, ?, 1, ?)
                on conflict(repo_full_name, pr_number) do update set
                    url = excluded.url,
                    enabled = 1
                """,
                (url, owner, repo, repo_full_name, pr_number, now),
            )
            await db.commit()
        watch = await self.get_watched_pr_by_ref(
            repo_full_name=repo_full_name, pr_number=pr_number
        )
        if not watch:
            raise RuntimeError("Failed to save watched PR")
        return watch

    async def list_watched_prs(self) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, url, owner, repo, repo_full_name, pr_number, enabled,
                    created_at, last_polled_at, last_poll_status, last_poll_summary,
                    last_seen_comment_id, last_webhook_status, last_webhook_summary,
                    webhook_id, webhook_url
                from watched_prs
                order by id desc
                """
            )
        return [self._watched_pr_from_row(row) for row in rows]

    async def get_watched_pr(self, watch_id: int) -> dict[str, object] | None:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, url, owner, repo, repo_full_name, pr_number, enabled,
                    created_at, last_polled_at, last_poll_status, last_poll_summary,
                    last_seen_comment_id, last_webhook_status, last_webhook_summary,
                    webhook_id, webhook_url
                from watched_prs
                where id = ?
                """,
                (watch_id,),
            )
        if not rows:
            return None
        return self._watched_pr_from_row(rows[0])

    async def get_watched_pr_by_ref(
        self, *, repo_full_name: str, pr_number: int
    ) -> dict[str, object] | None:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select id, url, owner, repo, repo_full_name, pr_number, enabled,
                    created_at, last_polled_at, last_poll_status, last_poll_summary,
                    last_seen_comment_id, last_webhook_status, last_webhook_summary,
                    webhook_id, webhook_url
                from watched_prs
                where repo_full_name = ? and pr_number = ?
                """,
                (repo_full_name, pr_number),
            )
        if not rows:
            return None
        return self._watched_pr_from_row(rows[0])

    async def delete_watched_pr(self, watch_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute("delete from watched_prs where id = ?", (watch_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_watched_pr_webhook(
        self,
        watch_id: int,
        *,
        status: str,
        summary: str,
        webhook_id: int | None = None,
        webhook_url: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                update watched_prs
                set last_webhook_status = ?, last_webhook_summary = ?,
                    webhook_id = coalesce(?, webhook_id),
                    webhook_url = coalesce(?, webhook_url)
                where id = ?
                """,
                (status, summary, webhook_id, webhook_url, watch_id),
            )
            await db.commit()

    async def update_watched_pr_poll(
        self,
        watch_id: int,
        *,
        status: str,
        summary: str,
        last_seen_comment_id: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.database_path) as db:
            if last_seen_comment_id is None:
                await db.execute(
                    """
                    update watched_prs
                    set last_polled_at = ?, last_poll_status = ?,
                        last_poll_summary = ?
                    where id = ?
                    """,
                    (now, status, summary, watch_id),
                )
            else:
                await db.execute(
                    """
                    update watched_prs
                    set last_polled_at = ?, last_poll_status = ?,
                        last_poll_summary = ?, last_seen_comment_id = ?
                    where id = ?
                    """,
                    (now, status, summary, last_seen_comment_id, watch_id),
                )
            await db.commit()

    async def mark_processed_comment(
        self, *, comment_id: int, repo_full_name: str, pr_number: int
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                insert or ignore into processed_comments
                    (comment_id, repo_full_name, pr_number, processed_at)
                values (?, ?, ?, ?)
                """,
                (comment_id, repo_full_name, pr_number, now),
            )
            await db.commit()

    async def load_session(
        self, *, repo_full_name: str, pr_number: int
    ) -> SessionState | None:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                select state_json from pr_sessions
                where repo_full_name = ? and pr_number = ?
                """,
                (repo_full_name, pr_number),
            )
        if not rows:
            return None
        return SessionState.model_validate_json(rows[0][0])

    async def save_session(self, state: SessionState) -> None:
        state.updated_at = datetime.now(timezone.utc)
        state_json = state.model_dump_json()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                insert into pr_sessions
                    (repo_full_name, pr_number, state_json, updated_at)
                values (?, ?, ?, ?)
                on conflict(repo_full_name, pr_number) do update set
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.repo_full_name,
                    state.pr_number,
                    state_json,
                    state.updated_at.isoformat(),
                ),
            )
            await db.commit()

    async def debug_dump(self) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                "select repo_full_name, pr_number, state_json from pr_sessions"
            )
        return [
            {
                "repo_full_name": repo,
                "pr_number": pr_number,
                "state": json.loads(state_json),
            }
            for repo, pr_number, state_json in rows
        ]

    @staticmethod
    async def _ensure_column(
        db: aiosqlite.Connection, table: str, column: str, column_type: str
    ) -> None:
        rows = await db.execute_fetchall(f"pragma table_info({table})")
        existing = {str(row[1]) for row in rows}
        if column not in existing:
            await db.execute(f"alter table {table} add column {column} {column_type}")

    @staticmethod
    def _event_from_row(row: tuple[object, ...]) -> dict[str, object]:
        (
            event_id,
            received_at,
            source,
            event_type,
            delivery_id,
            status,
            summary,
            details_json,
        ) = row
        return {
            "id": event_id,
            "received_at": received_at,
            "source": source,
            "event_type": event_type,
            "delivery_id": delivery_id,
            "status": status,
            "summary": summary,
            "details": json.loads(str(details_json)),
        }

    @staticmethod
    def _watched_pr_from_row(row: tuple[object, ...]) -> dict[str, object]:
        (
            watch_id,
            url,
            owner,
            repo,
            repo_full_name,
            pr_number,
            enabled,
            created_at,
            last_polled_at,
            last_poll_status,
            last_poll_summary,
            last_seen_comment_id,
            last_webhook_status,
            last_webhook_summary,
            webhook_id,
            webhook_url,
        ) = row
        return {
            "id": watch_id,
            "url": url,
            "owner": owner,
            "repo": repo,
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "enabled": bool(enabled),
            "created_at": created_at,
            "last_polled_at": last_polled_at,
            "last_poll_status": last_poll_status,
            "last_poll_summary": last_poll_summary,
            "last_seen_comment_id": last_seen_comment_id,
            "last_webhook_status": last_webhook_status,
            "last_webhook_summary": last_webhook_summary,
            "webhook_id": webhook_id,
            "webhook_url": webhook_url,
        }

    @staticmethod
    def _watched_repo_from_row(row: tuple[object, ...]) -> dict[str, object]:
        (
            watch_id,
            url,
            owner,
            repo,
            repo_full_name,
            enabled,
            created_at,
            last_webhook_status,
            last_webhook_summary,
            webhook_id,
            webhook_url,
        ) = row
        return {
            "id": watch_id,
            "url": url,
            "owner": owner,
            "repo": repo,
            "repo_full_name": repo_full_name,
            "enabled": bool(enabled),
            "created_at": created_at,
            "last_webhook_status": last_webhook_status,
            "last_webhook_summary": last_webhook_summary,
            "webhook_id": webhook_id,
            "webhook_url": webhook_url,
        }
