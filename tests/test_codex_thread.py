from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from pr_comment_codex_bot.codex_thread import CodexThreadClient, CodexThreadTurnError
from pr_comment_codex_bot.settings import Settings


class FakeWebSocket:
    def __init__(self, *, include_final_answer: bool = True) -> None:
        self.sent_methods: list[str] = []
        self.sent_params: list[dict[str, Any]] = []
        self._messages: list[str] = []
        self.include_final_answer = include_final_answer

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        method = message["method"]
        params = message["params"]
        self.sent_methods.append(method)
        self.sent_params.append(params)
        if method == "thread/start":
            self._messages.append(
                self._response(message["id"], {"thread": {"id": "thread-new"}})
            )
        elif method == "thread/resume":
            self._messages.append(
                self._response(message["id"], {"thread": {"id": params["threadId"]}})
            )
        elif method == "turn/start":
            self._messages.append(self._response(message["id"], {}))
            if self.include_final_answer:
                self._messages.append(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "item/completed",
                            "params": {
                                "item": {
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": "done",
                                }
                            },
                        }
                    )
                )
            self._messages.append(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "turn/completed",
                        "params": {},
                    }
                )
            )
        else:
            self._messages.append(self._response(message["id"], {}))

    async def recv(self) -> str:
        return self._messages.pop(0)

    @staticmethod
    def _response(request_id: str, result: dict[str, Any]) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


class FakeConnect:
    def __init__(self, ws: FakeWebSocket) -> None:
        self.ws = ws

    async def __aenter__(self) -> FakeWebSocket:
        return self.ws

    async def __aexit__(self, *_: Any) -> None:
        return None


class CodexThreadClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_debate_thread_resumes_before_new_turn(self) -> None:
        ws = FakeWebSocket()
        client = CodexThreadClient(Settings(codex_thread_timeout_seconds=1))

        with patch(
            "pr_comment_codex_bot.codex_thread.websockets.connect",
            return_value=FakeConnect(ws),
        ):
            result = await client._run_codex_thread_turn(
                prompt="reply",
                sandbox="read-only",
                effort="medium",
                thread_id="thread-existing",
            )

        self.assertEqual(result["thread_id"], "thread-existing")
        self.assertNotIn("thread/start", ws.sent_methods)
        self.assertEqual(ws.sent_methods, ["initialize", "thread/resume", "turn/start"])
        resume_params = ws.sent_params[1]
        self.assertEqual(resume_params["threadId"], "thread-existing")
        self.assertEqual(resume_params["sandbox"], "read-only")
        turn_params = ws.sent_params[2]
        self.assertEqual(turn_params["threadId"], "thread-existing")

    async def test_missing_debate_thread_starts_one_before_turn(self) -> None:
        ws = FakeWebSocket()
        client = CodexThreadClient(Settings(codex_thread_timeout_seconds=1))

        with patch(
            "pr_comment_codex_bot.codex_thread.websockets.connect",
            return_value=FakeConnect(ws),
        ):
            result = await client._run_codex_thread_turn(
                prompt="first",
                sandbox="read-only",
                effort="medium",
            )

        self.assertEqual(result["thread_id"], "thread-new")
        self.assertEqual(ws.sent_methods, ["initialize", "thread/start", "turn/start"])
        turn_params = ws.sent_params[2]
        self.assertEqual(turn_params["threadId"], "thread-new")

    async def test_thread_name_is_set_when_provided(self) -> None:
        ws = FakeWebSocket()
        client = CodexThreadClient(Settings(codex_thread_timeout_seconds=1))

        with patch(
            "pr_comment_codex_bot.codex_thread.websockets.connect",
            return_value=FakeConnect(ws),
        ):
            await client._run_codex_thread_turn(
                prompt="first",
                sandbox="read-only",
                effort="medium",
                thread_name="Debate acme/widgets#42",
            )

        self.assertEqual(
            ws.sent_methods,
            ["initialize", "thread/start", "thread/name/set", "turn/start"],
        )
        name_params = ws.sent_params[2]
        self.assertEqual(name_params["threadId"], "thread-new")
        self.assertEqual(name_params["name"], "Debate acme/widgets#42")

    async def test_turn_error_keeps_started_thread_id(self) -> None:
        ws = FakeWebSocket(include_final_answer=False)
        client = CodexThreadClient(Settings(codex_thread_timeout_seconds=1))

        with patch(
            "pr_comment_codex_bot.codex_thread.websockets.connect",
            return_value=FakeConnect(ws),
        ):
            with self.assertRaises(CodexThreadTurnError) as error:
                await client._run_codex_thread_turn(
                    prompt="first",
                    sandbox="read-only",
                    effort="medium",
                )

        self.assertEqual(error.exception.thread_id, "thread-new")
        self.assertIn(
            "completed without a final answer",
            str(error.exception),
        )


if __name__ == "__main__":
    unittest.main()
