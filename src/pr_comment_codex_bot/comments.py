from __future__ import annotations

from .models import CodexJobResult

BOT_COMMENT_MARKER = "<!-- pr-comment-codex-bot -->"


def is_marked_bot_comment(body: str | None) -> bool:
    return BOT_COMMENT_MARKER in (body or "")


def mark_bot_comment(body: str) -> str:
    if is_marked_bot_comment(body):
        return body
    return f"{BOT_COMMENT_MARKER}\n{body}"


def implementation_finished_comment(result: CodexJobResult) -> str:
    if result.status.lower() == "failed" or result.error:
        body = ["Implementation did not complete cleanly."]
        if result.job_id:
            body.append(f"- Job: `{result.job_id}`")
        if result.thread_id:
            body.append(f"- Codex thread: `{result.thread_id}`")
        body.append(f"- Error: {result.error or result.status}")
        return mark_bot_comment("\n".join(body))

    body = ["Implementation complete."]
    if result.summary:
        body.extend(["", result.summary])
    details: list[str] = []
    if result.branch:
        details.append(f"Branch: `{result.branch}`")
    if result.commit_sha:
        details.append(f"Commit: `{result.commit_sha}`")
    if result.thread_id:
        details.append(f"Codex thread: `{result.thread_id}`")
    if result.job_id:
        details.append(f"Job: `{result.job_id}`")
    if details:
        body.extend(["", *[f"- {item}" for item in details]])
    if result.changed_files:
        body.extend(["", "Changed files:", *[f"- `{path}`" for path in result.changed_files]])
    if result.tests:
        body.extend(["", "Tests run:", *[f"- `{test}`" for test in result.tests]])
    return mark_bot_comment("\n".join(body))
