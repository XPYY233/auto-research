from __future__ import annotations

import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModuleOwnershipContractTests(unittest.TestCase):
    def test_module_ownership_is_complete_and_path_scoped(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "config" / "module-ownership.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["schema"], "auto-research-module-ownership-v1")
        allowed = set(contract["allowed_statuses"])
        modules = contract["modules"]
        self.assertGreaterEqual(len(modules), 12)
        self.assertEqual(len({row["id"] for row in modules}), len(modules))
        for row in modules:
            self.assertIn(row["status"], allowed)
            self.assertTrue(row["owner"])
            self.assertTrue(row["authority"])
            self.assertTrue(row["paths"])
            for pattern in row["paths"]:
                self.assertFalse(Path(pattern).is_absolute())
                self.assertNotIn("..", Path(pattern).parts)

    def test_platform_authorities_remain_thin(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "config" / "module-ownership.json").read_text(encoding="utf-8")
        )
        rows = {row["id"]: row for row in contract["modules"]}
        for platform in ("macos-shell", "windows-shell"):
            authority = rows[platform]["authority"].casefold()
            self.assertNotIn("signature", authority)
            self.assertNotIn("ranking", authority)
            self.assertNotIn("prompt", authority)
        self.assertEqual(rows["browser-workbench-compatibility"]["status"], "retired")


if __name__ == "__main__":
    unittest.main()
