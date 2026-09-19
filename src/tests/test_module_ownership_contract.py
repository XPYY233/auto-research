from __future__ import annotations

import json
import fnmatch
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

    def test_every_runtime_file_has_a_resolvable_owner(self) -> None:
        contract = json.loads((PROJECT_ROOT / "config/module-ownership.json").read_text())
        missing, ambiguous = [], []
        for base in ("src/auto_research", "desktop/macos", "desktop/windows"):
            for path in (PROJECT_ROOT / base).rglob("*"):
                if path.suffix not in {".py", ".js", ".html", ".css"} or any(part in {"tests", "__pycache__", "build", "dist", ".venv", "releases"} for part in path.parts):
                    continue
                relative = path.relative_to(PROJECT_ROOT).as_posix()
                matches = []
                for row in contract["modules"]:
                    for pattern in row["paths"]:
                        if relative == pattern or relative.startswith(pattern + "/") or fnmatch.fnmatchcase(relative, pattern):
                            matches.append((len(pattern.replace("*", "")), row["id"]))
                if not matches:
                    missing.append(relative)
                    continue
                specificity = max(score for score, _ in matches)
                owners = {owner for score, owner in matches if score == specificity}
                if len(owners) != 1:
                    ambiguous.append((relative, sorted(owners)))
        self.assertEqual([], missing, "runtime files without responsibility")
        self.assertEqual([], ambiguous, "ambiguous ownership")

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
