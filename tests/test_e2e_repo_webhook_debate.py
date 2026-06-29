from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient


class MockHTTPServer:
    def __init__(self, handler_factory: Any) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(self))
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "MockHTTPServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length") or 0)
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode())


def write_json(
    handler: BaseHTTPRequestHandler, payload: dict[str, Any] | list[dict[str, Any]]
) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(200)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_text(handler: BaseHTTPRequestHandler, text: str) -> None:
    body = text.encode()
    handler.send_response(200)
    handler.send_header("content-type", "text/plain")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class MockGitHubState:
    def __init__(self) -> None:
        self.hooks: list[dict[str, Any]] = []
        self.hook_deliveries: list[dict[str, Any]] = [
            {
                "id": 222,
                "event": "ping",
                "action": None,
                "status": "OK",
                "status_code": 200,
                "delivered_at": "2026-06-26T18:00:00Z",
                "duration": 0.2,
                "redelivery": False,
            }
        ]
        self.created_hooks: list[dict[str, Any]] = []
        self.updated_hooks: list[dict[str, Any]] = []
        self.collaborator_requests: list[dict[str, Any]] = []
        self.accepted_invitations: list[int] = []
        self.posted_comments: list[dict[str, Any]] = []


class MockCodexThreadState:
    def __init__(self) -> None:
        self.debate_payloads: list[dict[str, Any]] = []


def github_handler_factory(state: MockGitHubState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            accept = self.headers.get("accept", "")
            if path == "/repos/acme/widgets/hooks":
                write_json(self, state.hooks)
            elif path == "/repos/acme/widgets/hooks/1234/deliveries":
                write_json(self, state.hook_deliveries)
            elif path == "/user/repository_invitations":
                write_json(
                    self,
                    [
                        {
                            "id": 555,
                            "repository": {"full_name": "acme/widgets"},
                        }
                    ],
                )
            elif path == "/repos/acme/widgets/pulls/42" and "diff" in accept:
                write_text(self, "diff --git a/app.py b/app.py\n+print('codex')\n")
            elif path == "/repos/acme/widgets/pulls/42":
                write_json(self, pull_request_payload())
            elif path == "/repos/acme/widgets/pulls/42/files":
                write_json(
                    self,
                    [
                        {
                            "filename": "app.py",
                            "status": "modified",
                            "changes": 1,
                            "patch": "+print('codex')",
                        }
                    ],
                )
            elif path == "/repos/acme/widgets/issues/42/comments":
                write_json(
                    self,
                    [
                        {
                            "id": 990,
                            "body": "Prior context: keep the behavior minimal.",
                            "user": {"login": "reviewer"},
                            "created_at": "2026-06-26T18:00:00Z",
                        },
                        latest_comment_payload(),
                    ],
                )
            elif path == "/repos/acme/widgets/pulls/42/comments":
                write_json(
                    self,
                    [
                        {
                            "id": 551,
                            "body": "Inline review context",
                            "path": "app.py",
                            "line": 1,
                            "user": {"login": "reviewer"},
                            "created_at": "2026-06-26T18:10:00Z",
                        }
                    ],
                )
            elif path == "/repos/acme/widgets/pulls/42/reviews":
                write_json(
                    self,
                    [
                        {
                            "id": 44,
                            "state": "COMMENTED",
                            "body": "Review summary context",
                            "user": {"login": "reviewer"},
                            "submitted_at": "2026-06-26T18:11:00Z",
                        }
                    ],
                )
            elif path == "/repos/acme/widgets/pulls/42/commits":
                write_json(
                    self,
                    [
                        {
                            "sha": "abc123",
                            "html_url": "https://github.com/acme/widgets/commit/abc123",
                            "commit": {
                                "message": "Add initial widget plan",
                                "author": {"name": "Alice"},
                            },
                        }
                    ],
                )
            else:
                self.send_error(404, f"Unhandled GitHub GET {path}")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            body = read_json_body(self)
            if path == "/repos/acme/widgets/hooks":
                state.created_hooks.append(body)
                state.hooks.append(
                    {
                        "id": 1234,
                        "name": "web",
                        "active": True,
                        "events": body["events"],
                        "config": body["config"],
                        "last_response": {
                            "code": 200,
                            "status": "active",
                            "message": "OK",
                        },
                        "created_at": "2026-06-26T18:00:00Z",
                        "updated_at": "2026-06-26T18:00:00Z",
                    }
                )
                write_json(
                    self,
                    {
                        "id": 1234,
                        "events": body["events"],
                        "config": body["config"],
                    },
                )
            elif path == "/repos/acme/widgets/issues/42/comments":
                state.posted_comments.append(body)
                write_json(self, {"id": 777, "body": body["body"]})
            else:
                self.send_error(404, f"Unhandled GitHub POST {path}")

        def do_PUT(self) -> None:
            path = urlparse(self.path).path
            body = read_json_body(self)
            if path == "/repos/acme/widgets/collaborators/codex-bot":
                state.collaborator_requests.append(body)
                write_json(
                    self,
                    {
                        "id": 555,
                        "repository": {"full_name": "acme/widgets"},
                        "permissions": body,
                    },
                )
            else:
                self.send_error(404, f"Unhandled GitHub PUT {path}")

        def do_PATCH(self) -> None:
            path = urlparse(self.path).path
            if path == "/user/repository_invitations/555":
                state.accepted_invitations.append(555)
                self.send_response(204)
                self.end_headers()
            elif path == "/repos/acme/widgets/hooks/1234":
                body = read_json_body(self)
                state.updated_hooks.append(body)
                state.hooks[0] = {
                    **state.hooks[0],
                    "events": body["events"],
                    "config": body["config"],
                    "updated_at": "2026-06-26T18:05:00Z",
                }
                write_json(self, state.hooks[0])
            else:
                self.send_error(404, f"Unhandled GitHub PATCH {path}")

    return Handler


def pull_request_payload() -> dict[str, Any]:
    return {
        "number": 42,
        "title": "codex plan debate",
        "body": "Please let codex interview this plan.",
        "html_url": "https://github.com/acme/widgets/pull/42",
        "url": "https://api.github.local/repos/acme/widgets/pulls/42",
        "base": {"ref": "main"},
        "head": {
            "ref": "feature/widget",
            "sha": "abc123",
            "repo": {"clone_url": "https://github.com/acme/widgets.git"},
        },
    }


def latest_comment_payload() -> dict[str, Any]:
    return {
        "id": 991,
        "body": "codex please debate this plan",
        "user": {"login": "alice"},
        "created_at": "2026-06-26T18:12:00Z",
    }


@contextlib.contextmanager
def isolated_app_env(github_url: str):
    old_env = os.environ.copy()
    module_names = [
        name
        for name in list(sys.modules)
        if name == "pr_comment_codex_bot.main"
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "comment-style.md").write_text("Ask concise questions.\n")
        (tmp / "tunnel-info.json").write_text(
            json.dumps(
                {
                    "status": "ready",
                    "provider": "test",
                    "public_url": "https://bot.example.test",
                }
            )
        )
        os.environ.update(
            {
                "DATABASE_PATH": str(tmp / "bot.sqlite3"),
                "TUNNEL_INFO_PATH": str(tmp / "tunnel-info.json"),
                "COMMENT_STYLE_PATH": str(tmp / "comment-style.md"),
                "GITHUB_API_URL": github_url,
                "GITHUB_TOKEN": "test-token",
                "GITHUB_USE_GH_CLI_TOKEN": "false",
                "GITHUB_BOT_LOGIN": "codex-bot",
                "GITHUB_REPO_ADMIN_TOKEN": "admin-token",
                "GITHUB_REPO_ADMIN_COLLABORATOR_PERMISSION": "admin",
                "GITHUB_WEBHOOK_SECRET": "test-secret",
                "GITHUB_TRIGGER_PHRASE": "codex",
                "GITHUB_AUTO_SYNC_WEBHOOKS": "false",
                "CODEX_THREAD_WS_URL": "ws://127.0.0.1:1",
            }
        )
        for name in module_names:
            sys.modules.pop(name, None)
        import pr_comment_codex_bot.main as main

        importlib.reload(main)
        try:
            yield main
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            sys.modules.pop("pr_comment_codex_bot.main", None)


def signed_github_body(payload: dict[str, Any], secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, f"sha256={digest}"


class RepoWebhookDebateE2ETests(unittest.TestCase):
    def test_repo_watch_webhook_comment_debate_and_reply(self) -> None:
        github_state = MockGitHubState()
        codex_state = MockCodexThreadState()

        with (
            MockHTTPServer(lambda _: github_handler_factory(github_state)) as github,
            isolated_app_env(github.base_url) as main,
            TestClient(main.app) as client,
        ):
            import pr_comment_codex_bot.service as service_module
            from pr_comment_codex_bot.models import (
                CodexDebateResult,
                InterviewDecision,
            )

            class FakeCodexThreadClient:
                def __init__(self, settings: Any) -> None:
                    self.settings = settings

                async def run_debate(
                    self,
                    *,
                    context: Any,
                    session: Any,
                    comment_style_guide: str,
                ) -> CodexDebateResult:
                    codex_state.debate_payloads.append(
                        {
                            "repo": context.repo.model_dump(),
                            "pr": context.pr.model_dump(),
                            "context": {
                                "issue_comments": context.issue_comments,
                                "commits": context.commits,
                                "diff": context.diff,
                            },
                            "comment_style_guide": comment_style_guide,
                        }
                    )
                    return CodexDebateResult(
                        job_id="codex-thread-debate-1",
                        thread_id="codex-thread-debate-1",
                        decision=InterviewDecision.model_validate(
                            {
                                "status": "needs_answer",
                                "reply_body": "Question 1: Should we keep it minimal?",
                                "questions": [
                                    {
                                        "question": "Should we keep it minimal?",
                                        "recommended_answer": "Yes.",
                                        "why_it_matters": "It limits scope.",
                                    }
                                ],
                                "resolved_decisions": ["Repo context was read."],
                                "unresolved_decisions": ["Final behavior wording."],
                                "codebase_evidence": ["app.py changed in the PR."],
                                "implementation_brief": None,
                            }
                        ),
                    )

            add_response = client.post(
                "/watched-repos", json={"url": "https://github.com/acme/widgets"}
            )
            self.assertEqual(add_response.status_code, 200, add_response.text)
            self.assertEqual(github_state.collaborator_requests, [{"permission": "admin"}])
            self.assertEqual(github_state.accepted_invitations, [555])
            self.assertEqual(len(github_state.created_hooks), 1)
            self.assertEqual(
                github_state.created_hooks[0]["config"]["url"],
                "https://bot.example.test/webhooks/github",
            )
            self.assertEqual(
                set(github_state.created_hooks[0]["events"]),
                {"issue_comment", "pull_request"},
            )
            dashboard_html = client.get("/").text
            self.assertIn("const eventId = String(event.id ?? \"\");", dashboard_html)
            self.assertNotIn("event.id.slice", dashboard_html)
            self.assertIn("grid-column: 1 / -1", dashboard_html)
            self.assertIn("min-height: 560px", dashboard_html)
            self.assertIn("max-height: 460px", dashboard_html)
            self.assertIn("max-width: 100%", dashboard_html)
            self.assertIn("allEvents.slice(0, 12)", dashboard_html)
            self.assertIn('data-sync="${esc(watch.id)}"', dashboard_html)
            self.assertIn('data-diagnose="${esc(watch.id)}"', dashboard_html)
            self.assertIn('id="sync-all-btn"', dashboard_html)

            async def healthy_tunnel(_: str | None) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "health_url": "https://bot.example.test/healthz",
                    "status_code": 200,
                }

            main.service._diagnose_tunnel_health = healthy_tunnel
            diagnostics_response = client.get(
                f"/watched-repos/{add_response.json()['id']}/diagnostics"
            )
            self.assertEqual(
                diagnostics_response.status_code, 200, diagnostics_response.text
            )
            diagnostics = diagnostics_response.json()
            self.assertEqual(diagnostics["status"], "ok")
            self.assertEqual(diagnostics["problems"], [])
            self.assertEqual(diagnostics["github"]["matching_hook"]["id"], 1234)
            self.assertEqual(diagnostics["github"]["deliveries"][0]["status_code"], 200)

            watch_id = int(add_response.json()["id"])
            asyncio.run(
                main.storage.update_watched_repo_webhook(
                    watch_id,
                    status="updated",
                    summary="Stale webhook for test",
                    webhook_url="https://old.example.test/webhooks/github",
                )
            )
            sync_all_response = client.post("/watched-repos/sync")
            self.assertEqual(sync_all_response.status_code, 200, sync_all_response.text)
            self.assertEqual(sync_all_response.json()["watch_ids"], [watch_id])
            self.assertEqual(
                github_state.updated_hooks[-1]["config"]["url"],
                "https://bot.example.test/webhooks/github",
            )

            payload = {
                "action": "created",
                "repository": {"full_name": "acme/widgets"},
                "issue": {"number": 42, "pull_request": {"url": "ignored"}},
                "comment": latest_comment_payload(),
            }
            body, signature = signed_github_body(payload, "test-secret")
            with patch.object(service_module, "CodexThreadClient", FakeCodexThreadClient):
                webhook_response = client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "x-github-event": "issue_comment",
                        "x-github-delivery": "delivery-1",
                        "x-hub-signature-256": signature,
                    },
                )
            self.assertEqual(webhook_response.status_code, 200, webhook_response.text)

            self.assertEqual(len(codex_state.debate_payloads), 1)
            debate_payload = codex_state.debate_payloads[0]
            self.assertEqual(debate_payload["repo"]["full_name"], "acme/widgets")
            self.assertEqual(debate_payload["pr"]["number"], 42)
            self.assertIn(
                "Prior context",
                debate_payload["context"]["issue_comments"][0]["body"],
            )
            self.assertEqual(debate_payload["context"]["commits"][0]["sha"], "abc123")

            self.assertEqual(len(github_state.posted_comments), 1)
            self.assertIn("Question 1", github_state.posted_comments[0]["body"])
            self.assertIn(
                "<!-- pr-comment-codex-bot -->",
                github_state.posted_comments[0]["body"],
            )

            sessions = client.get("/debug/sessions").json()
            self.assertEqual(
                sessions[0]["state"]["debate_thread_id"], "codex-thread-debate-1"
            )
            self.assertEqual(sessions[0]["state"]["status"], "interviewing")

            current_sessions = client.get("/sessions/current").json()
            self.assertEqual(current_sessions[0]["repo_full_name"], "acme/widgets")
            self.assertEqual(current_sessions[0]["pr_number"], 42)
            self.assertEqual(
                current_sessions[0]["threads"][0]["thread_id"],
                "codex-thread-debate-1",
            )
            self.assertEqual(
                current_sessions[0]["threads"][0]["open_url"],
                "/codex/threads/codex-thread-debate-1/open",
            )
            sessions_page = client.get("/sessions")
            self.assertEqual(sessions_page.status_code, 200)
            self.assertIn("Current Sessions", sessions_page.text)
            open_response = client.get(
                "/codex/threads/codex-thread-debate-1/open",
                follow_redirects=False,
            )
            self.assertEqual(open_response.status_code, 307)
            self.assertEqual(
                open_response.headers["location"],
                "codex://threads/codex-thread-debate-1",
            )
            missing_open_response = client.get(
                "/codex/threads/not-tracked/open",
                follow_redirects=False,
            )
            self.assertEqual(missing_open_response.status_code, 404)

            marked_payload = {
                "action": "created",
                "repository": {"full_name": "acme/widgets"},
                "issue": {"number": 42, "pull_request": {"url": "ignored"}},
                "comment": {
                    "id": 992,
                    "body": (
                        "<!-- pr-comment-codex-bot -->\n"
                        "This bot comment includes codex but should not recurse."
                    ),
                    "user": {"login": "alice"},
                    "created_at": "2026-06-26T18:13:00Z",
                },
            }
            marked_body, marked_signature = signed_github_body(
                marked_payload, "test-secret"
            )
            marked_response = client.post(
                "/webhooks/github",
                content=marked_body,
                headers={
                    "content-type": "application/json",
                    "x-github-event": "issue_comment",
                    "x-github-delivery": "delivery-2",
                    "x-hub-signature-256": marked_signature,
                },
            )
            self.assertEqual(marked_response.status_code, 200, marked_response.text)
            self.assertEqual(len(codex_state.debate_payloads), 1)
            self.assertEqual(len(github_state.posted_comments), 1)

            events = client.get("/events?limit=1").json()
            self.assertEqual(events[0]["status"], "ignored")
            self.assertIn("marked bot comment", events[0]["summary"])

    def test_disabled_repo_ignores_webhook_comments(self) -> None:
        github_state = MockGitHubState()

        with (
            MockHTTPServer(lambda _: github_handler_factory(github_state)) as github,
            isolated_app_env(github.base_url) as main,
            TestClient(main.app) as client,
        ):
            add_response = client.post(
                "/watched-repos", json={"url": "https://github.com/acme/widgets"}
            )
            self.assertEqual(add_response.status_code, 200, add_response.text)
            watch_id = add_response.json()["id"]

            disable_response = client.patch(
                f"/watched-repos/{watch_id}", json={"enabled": False}
            )
            self.assertEqual(disable_response.status_code, 200, disable_response.text)
            self.assertFalse(disable_response.json()["enabled"])

            payload = {
                "action": "created",
                "repository": {"full_name": "acme/widgets"},
                "issue": {"number": 42, "pull_request": {"url": "ignored"}},
                "comment": latest_comment_payload(),
            }
            body, signature = signed_github_body(payload, "test-secret")
            webhook_response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-github-event": "issue_comment",
                    "x-github-delivery": "delivery-disabled",
                    "x-hub-signature-256": signature,
                },
            )
            self.assertEqual(webhook_response.status_code, 200, webhook_response.text)
            self.assertEqual(len(github_state.posted_comments), 0)

            events = client.get("/events?limit=1").json()
            self.assertEqual(events[0]["status"], "ignored")
            self.assertIn("disabled", events[0]["summary"])


if __name__ == "__main__":
    unittest.main()
