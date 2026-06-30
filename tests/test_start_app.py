from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.start_app as start_app


class StartAppTests(unittest.TestCase):
    def test_resolve_codex_bin_prefers_env_override(self) -> None:
        with patch.dict("os.environ", {"CODEX_APP_SERVER_BIN": "C:/Codex/codex.exe"}):
            self.assertEqual(start_app.resolve_codex_bin(), "C:/Codex/codex.exe")

    def test_resolve_codex_bin_uses_path_before_macos_bundle(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(start_app.shutil, "which", return_value="/usr/bin/codex"),
        ):
            self.assertEqual(start_app.resolve_codex_bin(), "/usr/bin/codex")

    def test_resolve_codex_bin_falls_back_to_macos_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_bin = Path(tmp) / "codex"
            codex_bin.write_text("", encoding="utf-8")
            with (
                patch.dict("os.environ", {}, clear=True),
                patch.object(start_app.shutil, "which", return_value=None),
                patch.object(start_app, "MACOS_CODEX_BIN", codex_bin),
            ):
                self.assertEqual(start_app.resolve_codex_bin(), str(codex_bin))


if __name__ == "__main__":
    unittest.main()
