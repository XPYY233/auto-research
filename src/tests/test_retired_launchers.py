from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHERS = (
    PROJECT_ROOT / "scripts" / "start_evidence_ui.command",
    PROJECT_ROOT / "scripts" / "start_readonly_ngrok.command",
)
EXPECTED_NOTICE = (
    "此入口已退役，请打开 Auto Research.app。\n"
    "导入或分享资料请使用经过签名验证的 .aresearch 资料包。\n"
)
FORBIDDEN_RUNTIME_FRAGMENTS = (
    "8765",
    "8766",
    "evidence-serve",
    "python",
    "npx",
    "ngrok",
    "authtoken",
    ".env",
    "token",
    "http://",
    "https://",
    "curl",
    "lsof",
    "open ",
    "source ",
    "export ",
)


class RetiredLauncherContractTests(unittest.TestCase):
    def test_launchers_are_inert_migration_notices(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher.name):
                text = launcher.read_text(encoding="utf-8")
                lowered = text.lower()

                self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
                self.assertIn("已退役", text)
                self.assertIn("Auto Research.app", text)
                self.assertIn(".aresearch", text)
                for fragment in FORBIDDEN_RUNTIME_FRAGMENTS:
                    self.assertNotIn(fragment, lowered)

    def test_launchers_only_print_the_retirement_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "historical-token-file"
            token_file.write_text("must-not-be-read", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "AUTO_RESEARCH_NGROK_ENV": str(token_file),
                "NGROK_AUTHTOKEN": "must-not-be-used",
            })

            for launcher in LAUNCHERS:
                with self.subTest(launcher=launcher.name):
                    completed = subprocess.run(
                        [str(launcher)],
                        cwd=PROJECT_ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, EXPECTED_NOTICE)
                    self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
