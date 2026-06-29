from __future__ import annotations

import hashlib
import hmac
import unittest

from pr_comment_codex_bot.security import has_trigger, verify_github_signature


class SecurityTests(unittest.TestCase):
    def test_verify_signature(self) -> None:
        body = b'{"ok": true}'
        secret = "secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(
            verify_github_signature(
                body=body,
                signature_header=f"sha256={digest}",
                webhook_secret=secret,
            )
        )

    def test_reject_bad_signature(self) -> None:
        self.assertFalse(
            verify_github_signature(
                body=b"payload",
                signature_header="sha256=bad",
                webhook_secret="secret",
            )
        )

    def test_empty_trigger_matches_everything(self) -> None:
        self.assertTrue(has_trigger("normal comment", ""))

    def test_trigger_is_case_insensitive(self) -> None:
        self.assertTrue(has_trigger("Please @CODEX-PLAN this", "@codex-plan"))


if __name__ == "__main__":
    unittest.main()
