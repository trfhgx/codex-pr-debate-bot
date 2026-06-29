import json
import tempfile
import unittest
from pathlib import Path

from pr_comment_codex_bot.service import PRCommentService
from pr_comment_codex_bot.settings import Settings
from pr_comment_codex_bot.storage import Storage


class ConnectionStatusTests(unittest.TestCase):
    def test_stale_webhook_shows_not_connected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tunnel_path = tmp / "tunnel-info.json"
            tunnel_path.write_text(
                json.dumps({"public_url": "https://new-tunnel.example"}),
                encoding="utf-8",
            )
            settings = Settings(
                database_path=tmp / "bot.sqlite3",
                tunnel_info_path=tunnel_path,
            )
            storage = Storage(settings.database_path)
            service = PRCommentService(settings=settings, storage=storage)

            watch = {
                "id": 1,
                "enabled": True,
                "webhook_url": "https://old-tunnel.example/webhooks/github",
                "last_webhook_status": "updated",
            }
            enriched = service.enrich_watched_repo(watch)

            self.assertFalse(enriched["connected"])
            self.assertEqual(enriched["connection_status"], "stale")
            self.assertIn("stale", enriched["connection_label"])

    def test_matching_webhook_shows_connected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tunnel_path = tmp / "tunnel-info.json"
            tunnel_path.write_text(
                json.dumps({"public_url": "https://live-tunnel.example"}),
                encoding="utf-8",
            )
            settings = Settings(
                database_path=tmp / "bot.sqlite3",
                tunnel_info_path=tunnel_path,
            )
            service = PRCommentService(settings=settings, storage=Storage(settings.database_path))

            watch = {
                "id": 1,
                "enabled": True,
                "webhook_url": "https://live-tunnel.example/webhooks/github",
                "last_webhook_status": "created",
            }
            enriched = service.enrich_watched_repo(watch)

            self.assertTrue(enriched["connected"])
            self.assertEqual(enriched["connection_status"], "connected")


if __name__ == "__main__":
    unittest.main()