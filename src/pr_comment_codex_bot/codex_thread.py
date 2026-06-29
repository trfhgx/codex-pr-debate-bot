from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import websockets
from pydantic import ValidationError

from .models import (
    CodexDebateResult,
    CodexJobResult,
    InterviewDecision,
    PullRequestContext,
    SessionState,
)
from .settings import Settings

DEBATE_INSTRUCTIONS = """
Interview the PR author relentlessly about every aspect of this plan until
there is shared understanding. Walk down each branch of the design tree,
resolving dependencies between decisions one by one.

If a question can be answered by exploring the codebase or PR context, answer
it from evidence instead of asking.

When human input is needed, ask 2-3 questions and include a recommended answer
for each one.

Use the full PR context: prior comments, review comments, reviews, commits,
changed files, and diff. Be critical. Do not accept vague plan language as
shared understanding.

Return a JSON object with this shape:
{
  "status": "needs_answer" | "ready_to_implement" | "blocked",
  "reply_body": "GitHub comment to post",
  "questions": [
    {
      "question": "...",
      "recommended_answer": "...",
      "why_it_matters": "..."
    }
  ],
  "resolved_decisions": ["..."],
  "unresolved_decisions": ["..."],
  "codebase_evidence": ["..."],
  "implementation_brief": "Only when ready_to_implement"
}
"""


class CodexThreadClient:
    """Codex execution adapter.

        Debate and implementation are routed through the official Codex app-server
        JSON-RPC thread protocol. This adapter does not call `/v1/responses`
        directly.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run_debate(
        self,
        *,
        context: PullRequestContext,
        session: SessionState,
        comment_style_guide: str,
    ) -> CodexDebateResult:
        debate_payload = {
            "repo": context.repo.model_dump(),
            "pr": context.pr.model_dump(),
            "thread_id": session.debate_thread_id,
            "job_id": session.debate_job_id,
            "session_state": session.model_dump(mode="json"),
            "comment_style_guide": comment_style_guide,
            "instructions": DEBATE_INSTRUCTIONS,
            "context": self._debate_context(context),
            "source": f"{self.settings.app_name}:debate",
        }
        prompt = (
            "You are the debate layer for a GitHub PR comment bot.\n"
            "Use the instructions and complete PR context below. Return only the "
            "requested JSON InterviewDecision, with no Markdown fence or prose "
            "around it.\n\n"
            f"{json.dumps(debate_payload, ensure_ascii=False)}"
        )
        result = await self._run_codex_thread_turn(
            prompt=prompt,
            sandbox="read-only",
            effort=self.settings.codex_thread_effort,
            thread_id=session.debate_thread_id,
        )
        raw = {
            "id": result["thread_id"],
            "thread_id": result["thread_id"],
            "status": "completed",
            "final_message": result["final_text"],
            "thread": result["thread"],
        }
        return self._parse_debate_result(raw)

    async def start_implementation(
        self,
        *,
        context: PullRequestContext,
        implementation_brief: str,
        comment_style_guide: str,
    ) -> CodexJobResult:
        implementation_payload = {
            "repo": context.repo.model_dump(),
            "pr": context.pr.model_dump(),
            "implementation_brief": implementation_brief,
            "comment_style_guide": comment_style_guide,
            "context": self._debate_context(context),
            "source": f"{self.settings.app_name}:implementation",
        }
        prompt = (
            "Create a Codex implementation thread for this GitHub PR request.\n"
            "Work in a new branch/worktree when needed, make the requested code "
            "changes, and push the branch. Do not post a GitHub comment yourself; "
            "the listener service will post the final summary. If GITHUB_TOKEN is "
            "available, use it for GitHub operations but never print it. Return only "
            "JSON with this shape: {\"status\":\"completed\"|\"failed\","
            "\"summary\":\"...\",\"branch\":\"...\",\"commit_sha\":\"...\","
            "\"changed_files\":[\"...\"],\"tests\":[\"...\"],\"error\":null|\"...\"}. "
            "If the branch was not pushed, status must be \"failed\" and error must "
            "explain the push blocker. Use the JSON context below.\n\n"
            f"{json.dumps(implementation_payload, ensure_ascii=False)}"
        )
        result = await self._run_codex_thread_turn(
            prompt=prompt,
            sandbox="danger-full-access",
            effort="high",
        )
        parsed = self._parse_job_result(result["final_text"])
        if parsed:
            parsed.thread_id = result["thread_id"]
            parsed.raw = result
            return parsed
        error = self._implementation_error_from_text(result["final_text"])
        return CodexJobResult(
            job_id=None,
            thread_id=result["thread_id"],
            status="failed" if error else "completed",
            summary=result["final_text"],
            error=error,
            raw=result,
        )

    async def get_job(self, job_id: str) -> CodexJobResult:
        return CodexJobResult(
            job_id=job_id,
            status="failed",
            error="Polling is not supported for Codex app-server thread jobs",
        )

    async def wait_for_completion(self, job_id: str) -> CodexJobResult:
        return await self.get_job(job_id)

    async def _run_codex_thread_turn(
        self,
        *,
        prompt: str,
        sandbox: str,
        effort: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        async with websockets.connect(
            self.settings.codex_thread_ws_url,
            max_size=20_000_000,
            open_timeout=10,
        ) as ws:
            request = _JsonRpcClient(ws)
            await request.call(
                "initialize",
                {
                    "clientInfo": {
                        "name": self.settings.app_name,
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            if thread_id:
                thread = {"id": thread_id, "resumed": True}
            else:
                thread_start_params = {
                    "cwd": str(self.settings.codex_thread_cwd),
                    "approvalPolicy": "never",
                    "sandbox": sandbox,
                    "sessionStartSource": "startup",
                    "threadSource": self.settings.app_name,
                    "ephemeral": False,
                }
                if self.settings.codex_thread_model:
                    thread_start_params["model"] = self.settings.codex_thread_model
                thread_response = await request.call("thread/start", thread_start_params)
                thread = thread_response["thread"]
                thread_id = thread["id"]
            await request.call(
                "turn/start",
                {
                    "threadId": thread_id,
                    "cwd": str(self.settings.codex_thread_cwd),
                    "effort": effort,
                    "input": [{"type": "text", "text": prompt}],
                },
            )
            final_text = await self._wait_for_final_text(request)
            return {"thread_id": thread_id, "thread": thread, "final_text": final_text}

    async def _wait_for_final_text(self, request: "_JsonRpcClient") -> str:
        deadline = asyncio.get_running_loop().time() + (
            self.settings.codex_thread_timeout_seconds
        )
        final_text = ""
        while asyncio.get_running_loop().time() < deadline:
            timeout = max(1.0, deadline - asyncio.get_running_loop().time())
            message = await request.recv(timeout=timeout)
            method = message.get("method")
            params = message.get("params") or {}
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("text"):
                    text = str(item["text"])
                    if item.get("phase") == "final_answer" or not final_text:
                        final_text = text
            if method == "turn/completed":
                if not final_text:
                    raise ValueError("Codex thread completed without a final answer")
                return final_text
        raise TimeoutError("Timed out waiting for Codex thread turn")

    @staticmethod
    def _debate_context(context: PullRequestContext) -> dict[str, Any]:
        return {
            "latest_comment": context.latest_comment,
            "issue_comments": context.issue_comments,
            "review_comments": context.review_comments,
            "reviews": context.reviews,
            "commits": context.commits,
            "files": context.files,
            "diff": context.diff[:180_000],
        }

    @classmethod
    def _parse_debate_result(cls, raw: dict[str, Any]) -> CodexDebateResult:
        decision_raw = (
            raw.get("decision")
            or raw.get("interview_decision")
            or raw.get("structured_decision")
        )
        if decision_raw is None and "status" in raw and "reply_body" in raw:
            decision_raw = raw
        if decision_raw is None:
            decision_raw = cls._extract_json_decision_text(raw)
        try:
            decision = InterviewDecision.model_validate(decision_raw)
        except ValidationError as exc:
            raise ValueError(
                "Codex thread debate response did not contain a valid "
                "InterviewDecision"
            ) from exc
        return CodexDebateResult(
            decision=decision,
            job_id=raw.get("job_id") or raw.get("id") or raw.get("debate_job_id"),
            thread_id=(
                raw.get("thread_id")
                or raw.get("codex_thread_id")
                or raw.get("debate_thread_id")
            ),
            status=raw.get("status") or "completed",
            raw=raw,
        )

    @staticmethod
    def _extract_json_decision_text(raw: dict[str, Any]) -> dict[str, Any]:
        text = (
            raw.get("output_text")
            or raw.get("final_message")
            or raw.get("summary")
            or raw.get("content")
            or raw.get("message")
            or ""
        )
        if isinstance(text, list):
            text = "\n".join(str(item) for item in text)
        text = str(text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Codex thread debate response did not include JSON")
        return json.loads(text[start : end + 1])

    @staticmethod
    def _parse_job_result(text: str) -> CodexJobResult | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return CodexJobResult.model_validate(payload)

    @staticmethod
    def _implementation_error_from_text(text: str) -> str | None:
        lowered = text.lower()
        blocker_markers = [
            "blocker:",
            "could not push",
            "couldn't push",
            "unable to push",
            "failed to push",
            "not pushed",
            "could not resolve host",
            "auth status",
            "token is invalid",
        ]
        if any(marker in lowered for marker in blocker_markers):
            return "Implementation thread completed locally but did not push cleanly"
        return None


class _JsonRpcClient:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.pending: dict[str, dict[str, Any]] = {}

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        await self.ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        )
        while True:
            if request_id in self.pending:
                message = self.pending.pop(request_id)
            else:
                message = await self.recv()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise RuntimeError(
                    f"Codex app-server {method} failed: {error.get('message') or error}"
                )
            return dict(message.get("result") or {})

    async def recv(self, *, timeout: float | None = None) -> dict[str, Any]:
        if timeout is None:
            raw = await self.ws.recv()
        else:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        message = json.loads(raw)
        if "id" in message and "method" not in message:
            self.pending[str(message["id"])] = message
        return message
