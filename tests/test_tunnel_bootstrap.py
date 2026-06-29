import unittest
from unittest.mock import patch

import scripts.start_with_tunnel as tunnel_script


class Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class TunnelBootstrapTests(unittest.TestCase):
    def test_public_tunnel_ready_checks_healthz(self) -> None:
        with patch.object(tunnel_script, "urlopen", return_value=Response(200)) as urlopen:
            self.assertTrue(tunnel_script.public_tunnel_ready("https://bot.example.test"))

        urlopen.assert_called_once_with("https://bot.example.test/healthz", timeout=5)

    def test_public_tunnel_ready_rejects_unreachable_tunnel(self) -> None:
        with patch.object(tunnel_script, "urlopen", side_effect=OSError("no route")):
            self.assertFalse(tunnel_script.public_tunnel_ready("https://bot.example.test"))


if __name__ == "__main__":
    unittest.main()
