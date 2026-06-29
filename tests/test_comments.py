from __future__ import annotations

import unittest

from pr_comment_codex_bot.comments import implementation_finished_comment
from pr_comment_codex_bot.models import CodexJobResult


class CommentTests(unittest.TestCase):
    def test_finished_comment_includes_summary_and_tests(self) -> None:
        body = implementation_finished_comment(
            CodexJobResult(
                job_id="job_1",
                thread_id="thr_1",
                status="completed",
                summary="Changed the parser.",
                branch="feature",
                commit_sha="abc123",
                changed_files=["backend/parser.py"],
                tests=["uv run python -m unittest"],
            )
        )
        self.assertIn("Implementation complete.", body)
        self.assertIn("Changed the parser.", body)
        self.assertIn("`backend/parser.py`", body)
        self.assertIn("`uv run python -m unittest`", body)


if __name__ == "__main__":
    unittest.main()
