import os
import signal
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import scripts.start_with_tunnel as tunnel_script


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class TunnelBootstrapTests(unittest.TestCase):
    def test_auto_provider_keeps_all_available_tunnel_commands(self) -> None:
        def which(command: str) -> str | None:
            return f"/usr/bin/{command}" if command in {"cloudflared", "ngrok", "npx"} else None

        with patch.object(tunnel_script.shutil, "which", side_effect=which):
            commands = tunnel_script.available_tunnel_commands("auto", 8088)

        self.assertEqual(
            [command.provider for command in commands],
            ["cloudflared", "ngrok", "localtunnel"],
        )

    def test_choose_tunnel_command_preserves_first_auto_provider(self) -> None:
        def which(command: str) -> str | None:
            return f"/usr/bin/{command}" if command in {"cloudflared", "npx"} else None

        with patch.object(tunnel_script.shutil, "which", side_effect=which):
            command = tunnel_script.choose_tunnel_command("auto", 8088)

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.provider, "cloudflared")

    def test_main_tries_next_auto_provider_after_tunnel_failure(self) -> None:
        started_commands: list[list[str]] = []

        async def wait_for_public_url(*_: object, **__: object) -> str:
            if len(started_commands) == 2:
                raise RuntimeError("bad tunnel")
            return "https://ok.loca.lt"

        def subprocess_start(command: list[str], *_: object, **__: object) -> FakeProcess:
            started_commands.append(command)
            return FakeProcess()

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(
                    os.environ,
                    {
                        "SERVER_PORT": "19088",
                        "TUNNEL_PROVIDER": "auto",
                        "TUNNEL_INFO_PATH": f"{tmp}/tunnel-info.json",
                    },
                ),
                patch.object(tunnel_script, "release_server_port"),
                patch.object(tunnel_script, "wait_for_server"),
                patch.object(
                    tunnel_script,
                    "available_tunnel_commands",
                    return_value=[
                        tunnel_script.TunnelCommand("cloudflared", ["cloudflared"]),
                        tunnel_script.TunnelCommand("localtunnel", ["localtunnel"]),
                    ],
                ),
                patch.object(
                    tunnel_script,
                    "subprocess_start",
                    side_effect=subprocess_start,
                ),
                patch.object(
                    tunnel_script,
                    "wait_for_public_url",
                    side_effect=wait_for_public_url,
                ),
                patch.object(
                    tunnel_script,
                    "wait_forever",
                    side_effect=KeyboardInterrupt,
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    tunnel_script.main()

        self.assertEqual(started_commands[1], ["cloudflared"])
        self.assertEqual(started_commands[2], ["localtunnel"])

    def test_public_tunnel_ready_checks_healthz(self) -> None:
        with patch.object(tunnel_script, "urlopen", return_value=Response(200)) as urlopen:
            self.assertTrue(tunnel_script.public_tunnel_ready("https://bot.example.test"))

        urlopen.assert_called_once_with("https://bot.example.test/healthz", timeout=5)

    def test_public_tunnel_ready_rejects_unreachable_tunnel(self) -> None:
        with patch.object(tunnel_script, "urlopen", side_effect=OSError("no route")):
            self.assertFalse(tunnel_script.public_tunnel_ready("https://bot.example.test"))

    def test_listening_pids_reads_lsof_and_skips_current_process(self) -> None:
        result = SimpleNamespace(
            returncode=0,
            stdout=f"123\n{os.getpid()}\n123\n",
            stderr="",
        )
        with patch.object(tunnel_script.subprocess, "run", return_value=result) as run:
            self.assertEqual(tunnel_script.listening_pids(8088), [123])

        run.assert_called_once_with(
            ["lsof", "-ti", "tcp:8088"],
            cwd=tunnel_script.PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_release_server_port_terminates_listening_pids(self) -> None:
        with (
            patch.object(tunnel_script, "listening_pids", return_value=[123, 456]),
            patch.object(tunnel_script, "terminate_pid") as terminate_pid,
            patch.object(tunnel_script, "process_exists", return_value=False),
        ):
            tunnel_script.release_server_port(8088)

        terminate_pid.assert_any_call(123, signal.SIGTERM)
        terminate_pid.assert_any_call(456, signal.SIGTERM)
        self.assertEqual(terminate_pid.call_count, 2)


if __name__ == "__main__":
    unittest.main()
