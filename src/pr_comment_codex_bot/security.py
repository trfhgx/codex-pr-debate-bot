from __future__ import annotations

import hashlib
import hmac


def verify_github_signature(
    *, body: bytes, signature_header: str | None, webhook_secret: str | None
) -> bool:
    if not webhook_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    actual = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, actual)


def has_trigger(body: str, trigger_phrase: str) -> bool:
    trigger = trigger_phrase.strip()
    return not trigger or trigger.lower() in body.lower()
