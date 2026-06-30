from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - script still works without dotenv.
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+")
PUBLIC_HEALTH_PATH = "/healthz"
PUBLIC_HEALTH_INTERVAL_SECONDS = 15
PUBLIC_HEALTH_FAILURE_LIMIT = 3


def main() -> None:
    if load_dotenv:
        load_dotenv(PROJECT_ROOT / ".env")

    host = os.getenv("SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("SERVER_PORT", "8088"))
    provider = os.getenv("TUNNEL_PROVIDER", "auto").lower()
    tunnel_info_path = Path(os.getenv("TUNNEL_INFO_PATH", "./tunnel-info.json"))
    if not tunnel_info_path.is_absolute():
        tunnel_info_path = PROJECT_ROOT / tunnel_info_path

    release_server_port(port)

    tunnel_commands = available_tunnel_commands(provider, port)
    write_tunnel_info(
        tunnel_info_path,
        {
            "status": "starting",
            "provider": (
                ", ".join(command.provider for command in tunnel_commands)
                if tunnel_commands
                else provider
            ),
            "public_url": None,
            "started_at": time.time(),
        },
    )

    server_env = os.environ.copy()
    server_env["TUNNEL_INFO_PATH"] = str(tunnel_info_path)
    server = subprocess_start(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pr_comment_codex_bot.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        env=server_env,
        capture_output=False,
    )

    tunnel = None
    tunnel_command = tunnel_commands[0] if tunnel_commands else None
    tunnel_attempt = 0
    try:
        wait_for_server(host, port)
        if not tunnel_commands:
            raise RuntimeError(
                "No tunnel provider found. Install cloudflared or ngrok, or keep "
                "using dashboard polling. With Homebrew: brew install cloudflared"
            )

        while True:
            tunnel_command = tunnel_commands[tunnel_attempt % len(tunnel_commands)]
            tunnel_attempt += 1
            try:
                write_tunnel_info(
                    tunnel_info_path,
                    {
                        "status": "starting",
                        "provider": tunnel_command.provider,
                        "public_url": None,
                        "started_at": time.time(),
                    },
                )
                tunnel = subprocess_start(
                    tunnel_command.command,
                    env=os.environ.copy(),
                    capture_output=True,
                )
                public_url = asyncio.run(
                    wait_for_public_url(
                        tunnel,
                        provider=tunnel_command.provider,
                        tunnel_info_path=tunnel_info_path,
                    )
                )
                webhook_url = f"{public_url.rstrip('/')}/webhooks/github"
                print()
                print("Tunnel ready.")
                print(f"Dashboard:   http://{host}:{port}/")
                print(f"Webhook URL: {webhook_url}")
                print()
                print("Paste the webhook URL into GitHub with content type application/json.")
                print("Press Ctrl-C to stop the server and tunnel.")
                print()
                wait_forever(
                    server,
                    tunnel,
                    public_url=public_url,
                    tunnel_info_path=tunnel_info_path,
                    provider=tunnel_command.provider,
                )
            except RuntimeError as exc:
                if server.poll() is not None:
                    raise RuntimeError("Server exited while starting tunnel") from exc
                next_command = tunnel_commands[tunnel_attempt % len(tunnel_commands)]
                action = (
                    f"Trying {next_command.provider} next"
                    if next_command.provider != tunnel_command.provider
                    else f"Restarting {tunnel_command.provider}"
                )
                print(f"{action} after tunnel failure: {exc}")
                write_tunnel_info(
                    tunnel_info_path,
                    {
                        "status": "restarting",
                        "provider": tunnel_command.provider,
                        "public_url": None,
                        "message": str(exc),
                        "next_provider": next_command.provider,
                        "restarting_at": time.time(),
                    },
                )
                terminate_process(tunnel)
                tunnel = None
                time.sleep(3)
                continue
    finally:
        write_tunnel_info(
            tunnel_info_path,
            {
                "status": "stopped",
                "provider": tunnel_command.provider if tunnel_command else provider,
                "public_url": None,
                "stopped_at": time.time(),
            },
        )
        terminate_process(tunnel)
        terminate_process(server)


class TunnelCommand:
    def __init__(self, provider: str, command: list[str]) -> None:
        self.provider = provider
        self.command = command


def choose_tunnel_command(provider: str, port: int) -> TunnelCommand | None:
    commands = available_tunnel_commands(provider, port)
    return commands[0] if commands else None


def available_tunnel_commands(provider: str, port: int) -> list[TunnelCommand]:
    if provider == "none":
        return []
    candidates = []
    if provider in {"auto", "cloudflared"}:
        candidates.append(
            TunnelCommand(
                "cloudflared",
                [
                    "cloudflared",
                    "tunnel",
                    "--url",
                    f"http://127.0.0.1:{port}",
                ],
            )
        )
    if provider in {"auto", "ngrok"}:
        candidates.append(
            TunnelCommand(
                "ngrok",
                ["ngrok", "http", str(port), "--log=stdout"],
            )
        )
    if provider in {"auto", "localtunnel"}:
        candidates.append(
            TunnelCommand(
                "localtunnel",
                ["npx", "--yes", "localtunnel", "--port", str(port)],
            )
        )
    available = [
        candidate for candidate in candidates if shutil.which(candidate.command[0])
    ]
    if provider not in {"auto", "cloudflared", "ngrok", "localtunnel"}:
        raise RuntimeError(f"Unknown TUNNEL_PROVIDER: {provider}")
    return available


def subprocess_start(command: list[str], env: dict[str, str], *, capture_output: bool):
    stdout = subprocess.PIPE if capture_output else None
    stderr = subprocess.STDOUT if capture_output else None
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True,
        bufsize=1,
        start_new_session=True,
    )


def release_server_port(port: int) -> None:
    pids = listening_pids(port)
    if not pids:
        return

    print(f"Port {port} is already in use; stopping: {', '.join(map(str, pids))}")
    for pid in pids:
        terminate_pid(pid, signal.SIGTERM)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        remaining = [pid for pid in pids if process_exists(pid)]
        if not remaining:
            return
        time.sleep(0.25)

    remaining = [pid for pid in pids if process_exists(pid)]
    if not remaining:
        return
    print(f"Port {port} is still held; killing: {', '.join(map(str, remaining))}")
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    for pid in remaining:
        terminate_pid(pid, kill_signal)


def listening_pids(port: int) -> list[int]:
    if os.name == "nt":
        return listening_pids_windows(port)
    return listening_pids_lsof(port)


def listening_pids_lsof(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("Could not check for existing port users because lsof is not installed.")
        return []

    if result.returncode not in {0, 1}:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not check port {port}: {message or 'lsof failed'}")

    current_pid = os.getpid()
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != current_pid and pid not in pids:
            pids.append(pid)
    return pids


def listening_pids_windows(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("Could not check for existing port users because netstat is not installed.")
        return []

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"Could not check port {port}: {message or 'netstat failed'}"
        )

    current_pid = os.getpid()
    pids: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[-2].upper()
        pid_text = parts[-1]
        if state != "LISTENING" or not local_address.endswith(f":{port}"):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid != current_pid and pid not in pids:
            pids.append(pid)
    return pids


def terminate_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise RuntimeError(f"Cannot stop process {pid}: permission denied") from exc


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_for_server(host: str, port: int) -> None:
    deadline = time.monotonic() + 30
    url = f"http://{host}:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except URLError:
            time.sleep(0.25)
    raise RuntimeError(f"Server did not become healthy at {url}")


async def wait_for_public_url(process, *, provider: str, tunnel_info_path: Path) -> str:
    if process.stdout is None:
        raise RuntimeError("Tunnel process has no stdout")
    deadline = time.monotonic() + 120
    public_url = None
    public_tunnel_is_ready = False
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                raise RuntimeError(f"{provider} exited before publishing a URL")
            await asyncio.sleep(0.1)
            continue
        print(f"[{provider}] {line}", end="")
        match = PUBLIC_URL_RE.search(line)
        if match:
            candidate = match.group(0).rstrip("/")
            if is_public_tunnel_url(provider, candidate):
                public_url = candidate
                public_tunnel_is_ready = wait_for_public_tunnel(candidate)
                if not public_tunnel_is_ready:
                    print(
                        f"[{provider}] Public URL published but /healthz is not "
                        "reachable yet; keeping the tunnel running."
                    )
                break
    if not public_url:
        raise RuntimeError(f"Timed out waiting for {provider} public URL")
    status = "ready" if public_tunnel_is_ready else "unhealthy"
    payload = {
        "status": status,
        "provider": provider,
        "public_url": public_url,
        "github_webhook_url": f"{public_url}/webhooks/github",
        "ready_at": time.time(),
    }
    if not public_tunnel_is_ready:
        payload["message"] = "Public URL exists but /healthz is not reachable yet"
        payload["failed_checks"] = 1
    write_tunnel_info(
        tunnel_info_path,
        payload,
    )
    return public_url


def wait_for_public_tunnel(public_url: str) -> bool:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if public_tunnel_ready(public_url):
            return True
        time.sleep(1)
    return False


def public_tunnel_ready(public_url: str) -> bool:
    health_url = f"{public_url.rstrip('/')}{PUBLIC_HEALTH_PATH}"
    try:
        with urlopen(health_url, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def is_public_tunnel_url(provider: str, url: str) -> bool:
    if provider == "cloudflared":
        return "trycloudflare.com" in url
    if provider == "ngrok":
        return "ngrok" in url
    if provider == "localtunnel":
        return "loca.lt" in url or "localtunnel" in url
    return True


def wait_forever(
    *processes,
    public_url: str | None = None,
    tunnel_info_path: Path | None = None,
    provider: str | None = None,
) -> None:
    tunnel_failures = 0
    next_public_health_check = 0.0
    while True:
        for process in processes:
            if process and process.poll() is not None:
                raise RuntimeError("A managed process exited")
        if public_url and time.monotonic() >= next_public_health_check:
            if public_tunnel_ready(public_url):
                tunnel_failures = 0
            else:
                tunnel_failures += 1
                if tunnel_info_path:
                    write_tunnel_info(
                        tunnel_info_path,
                        {
                            "status": "unhealthy",
                            "provider": provider,
                            "public_url": public_url,
                            "github_webhook_url": f"{public_url}/webhooks/github",
                            "failed_checks": tunnel_failures,
                            "checked_at": time.time(),
                        },
                    )
                if tunnel_failures >= PUBLIC_HEALTH_FAILURE_LIMIT:
                    raise RuntimeError(
                        f"Public tunnel health check failed {tunnel_failures} times"
                    )
            next_public_health_check = (
                time.monotonic() + PUBLIC_HEALTH_INTERVAL_SECONDS
            )
        time.sleep(1)


def terminate_process(process) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()


def write_tunnel_info(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
