from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
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


def main() -> None:
    if load_dotenv:
        load_dotenv(PROJECT_ROOT / ".env")

    host = os.getenv("SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("SERVER_PORT", "8088"))
    provider = os.getenv("TUNNEL_PROVIDER", "auto").lower()
    tunnel_info_path = Path(os.getenv("TUNNEL_INFO_PATH", "./tunnel-info.json"))
    if not tunnel_info_path.is_absolute():
        tunnel_info_path = PROJECT_ROOT / tunnel_info_path

    tunnel_command = choose_tunnel_command(provider, port)
    write_tunnel_info(
        tunnel_info_path,
        {
            "status": "starting",
            "provider": tunnel_command.provider if tunnel_command else provider,
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
    try:
        wait_for_server(host, port)
        if tunnel_command is None:
            raise RuntimeError(
                "No tunnel provider found. Install cloudflared or ngrok, or keep "
                "using dashboard polling. With Homebrew: brew install cloudflared"
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
        wait_forever(server, tunnel)
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
    if provider == "none":
        return None
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
    for candidate in candidates:
        if shutil.which(candidate.command[0]):
            return candidate
    if provider not in {"auto", "cloudflared", "ngrok", "localtunnel"}:
        raise RuntimeError(f"Unknown TUNNEL_PROVIDER: {provider}")
    return None


def subprocess_start(command: list[str], env: dict[str, str], *, capture_output: bool):
    import subprocess

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
                break
    if not public_url:
        raise RuntimeError(f"Timed out waiting for {provider} public URL")
    write_tunnel_info(
        tunnel_info_path,
        {
            "status": "ready",
            "provider": provider,
            "public_url": public_url,
            "github_webhook_url": f"{public_url}/webhooks/github",
            "ready_at": time.time(),
        },
    )
    return public_url


def is_public_tunnel_url(provider: str, url: str) -> bool:
    if provider == "cloudflared":
        return "trycloudflare.com" in url
    if provider == "ngrok":
        return "ngrok" in url
    if provider == "localtunnel":
        return "loca.lt" in url or "localtunnel" in url
    return True


def wait_forever(*processes) -> None:
    while True:
        for process in processes:
            if process and process.poll() is not None:
                raise RuntimeError("A managed process exited")
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
