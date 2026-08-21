from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    from finalize_runtime_candidate import finalize_candidate
finally:
    sys.path.pop(0)


class FinalizeRuntimeCandidateTests(unittest.TestCase):
    def test_runtime_manifest_assets_and_forbidden_module_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Auto Research"
            web = root / "_internal" / "auto_research" / "evidence" / "web"
            web.mkdir(parents=True)
            (root / "Auto Research.exe").write_bytes(b"fake-windows-executable")
            for name in ("index.html", "app.css", "workbench.css", "ai_consent.js", "fusion_review.js"):
                (web / name).write_text(name, encoding="utf-8")
            dependencies = Path(directory) / "resolved.txt"
            dependencies.write_text("pywebview==6.2.1\n", encoding="utf-8")
            toc = Path(directory) / "Analysis-00.toc"
            toc.write_text("safe module graph", encoding="utf-8")
            report = finalize_candidate(
                root,
                desktop_version="1.0.0-windows.rc.1",
                python_version="3.12.10",
                dependency_audit=dependencies,
                analysis_toc=toc,
            )
            self.assertEqual(report["component_count"], 9)
            self.assertTrue(report["forbidden_modules_absent"])
            self.assertTrue((root / "bundled-runtime-manifest.json").is_file())

    def test_forbidden_maintainer_module_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Auto Research"
            web = root / "_internal" / "auto_research" / "evidence" / "web"
            web.mkdir(parents=True)
            (root / "Auto Research.exe").write_bytes(b"fake")
            for name in ("index.html", "app.css", "workbench.css", "ai_consent.js", "fusion_review.js"):
                (web / name).write_text(name, encoding="utf-8")
            dependencies = Path(directory) / "resolved.txt"
            dependencies.write_text("safe", encoding="utf-8")
            toc = Path(directory) / "Analysis-00.toc"
            toc.write_text("auto_research.product.internal_preview_builder", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "禁止"):
                finalize_candidate(
                    root,
                    desktop_version="1.0.0-windows.rc.1",
                    python_version="3.12.10",
                    dependency_audit=dependencies,
                    analysis_toc=toc,
                )


if __name__ == "__main__":
    unittest.main()
