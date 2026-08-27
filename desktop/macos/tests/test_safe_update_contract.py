from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAFE_UPDATE_PATH = PROJECT_ROOT / "desktop" / "macos" / "safe_update.py"
SPEC = importlib.util.spec_from_file_location("auto_research_safe_update", SAFE_UPDATE_PATH)
assert SPEC is not None and SPEC.loader is not None
SAFE_UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SAFE_UPDATE)


class SafeUpdateContractTests(unittest.TestCase):
    def test_checks_every_published_javascript_and_no_legacy_controller(self) -> None:
        scripts = SAFE_UPDATE.production_javascript_assets()

        self.assertIn("src/auto_research/evidence/web/fusion_review.js", scripts)
        self.assertIn("src/auto_research/evidence/web/fusion_ai_experience.js", scripts)
        self.assertNotIn("src/auto_research/evidence/web/app.js", scripts)
        self.assertTrue(all((PROJECT_ROOT / path).is_file() for path in scripts))

    def test_rejects_missing_or_escaping_javascript_assets(self) -> None:
        cases = (
            {"schema": "auto-research-release-contract-v1", "web_assets": {}},
            {
                "schema": "auto-research-release-contract-v1",
                "web_assets": {"desktop/macos/safe_update.py.js": "ignored"},
            },
        )
        for contract in cases:
            with self.subTest(contract=contract):
                with tempfile.TemporaryDirectory(prefix="safe-update-contract-") as directory:
                    path = Path(directory) / "release-contract.json"
                    path.write_text(json.dumps(contract), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        SAFE_UPDATE.production_javascript_assets(path)


if __name__ == "__main__":
    unittest.main()
