from __future__ import annotations

from pathlib import Path


def update_env_values(path: Path, updates: dict[str, str | None]) -> None:
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    for index, line in enumerate(lines):
        for key, value in list(remaining.items()):
            if not line.startswith(f"{key}="):
                continue
            if value is None:
                lines[index] = f"{key}="
            else:
                lines[index] = f"{key}={value}"
            remaining.pop(key)
            break

    for key, value in remaining.items():
        if value is None:
            lines.append(f"{key}=")
        else:
            lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")