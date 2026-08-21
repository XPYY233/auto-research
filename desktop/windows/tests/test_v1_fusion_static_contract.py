from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WINDOWS_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
try:
    import shared_http_bridge
finally:
    sys.path.pop(0)
    sys.path.pop(0)


class WindowsV1FusionStaticContractTests(unittest.TestCase):
    def test_windows_serves_the_exact_v1_fusion_entry_assets(self) -> None:
        index = (PROJECT_ROOT / "src/auto_research/evidence/web/index.html").read_text(encoding="utf-8")
        self.assertEqual(index.count('data-view="paper"'), 2)
        self.assertEqual(index.count('data-view="search"'), 1)
        self.assertEqual(index.count('data-view="personal"'), 1)
        self.assertEqual(index.count('data-view="package"'), 1)
        self.assertLess(
            index.index('<script src="/static/ai_consent.js"></script>'),
            index.index('<script src="/static/fusion_review.js"></script>'),
        )
        self.assertEqual(
            shared_http_bridge._STATIC_FILES["/static/fusion_review.js"],
            ("fusion_review.js", "application/javascript; charset=utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
