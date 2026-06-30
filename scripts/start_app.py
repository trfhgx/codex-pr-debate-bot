from __future__ import annotations

import os
import shutil
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
MACOS_CODEX_BIN = Path("/Applications/Codex.app/Contents/Resources/codex")


def main() -> None:
    if load_dotenv:
        load_dotenv(PROJECT_ROOT / ".env")

    codex_ws_url = os.getenv("CODEX_THREAD_WS_URL", "ws://127.0.0.1:8765")
    codex_ready_url = codex_ws_url.replace("ws://", "http://").rstrip("/") + "/readyz"
    codex_process = None

    try:
        if is_ready(codex_ready_url):
            print(f"Codex app-server already ready: {codex_ws_url}")
        else:
            codex_process = start_codex_app_server(codex_ws_url)
            wait_until_ready(codex_ready_url, "Codex app-server")
            print(f"Codex app-server ready: {codex_ws_url}")

        run_listener()
    finally:
        terminate_process(codex_process)


def start_codex_app_server(codex_ws_url: str):
    codex_bin = resolve_codex_bin()
    env = os.environ.copy()
    return subprocess.Popen(
        [codex_bin, "app-server", "--listen", codex_ws_url],
        cwd=PROJECT_ROOT,
        env=env,
        start_new_session=True,
    )


def resolve_codex_bin() -> str:
    configured = os.getenv("CODEX_APP_SERVER_BIN")
    if configured:
        return configured
    path_codex = shutil.which("codex")
    if path_codex:
        return path_codex
    if MACOS_CODEX_BIN.exists():
        return str(MACOS_CODEX_BIN)
    return "codex"


def run_listener() -> None:
    subprocess.run(
        [sys.executable, "scripts/start_with_tunnel.py"],
        cwd=PROJECT_ROOT,
        check=True,
    )


def is_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1) as response:
            return response.status == 200
    except URLError:
        return False


def wait_until_ready(url: str, name: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if is_ready(url):
            return
        time.sleep(0.25)
    raise RuntimeError(f"{name} did not become ready at {url}")


def terminate_process(process) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
