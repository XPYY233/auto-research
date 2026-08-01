from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ProductRuntimeApiTests(unittest.TestCase):
    def run_isolated(self, source: str) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_runtime_api_does_not_load_publisher_exporters(self) -> None:
        self.run_isolated(
            "import sys\n"
            "from auto_research.product.runtime_api import open_active_official_repository\n"
            "assert callable(open_active_official_repository)\n"
            "assert 'auto_research.product.evidence_v12_export' not in sys.modules\n"
            "assert 'auto_research.product.internal_preview_builder' not in sys.modules\n"
        )

    def test_package_root_keeps_legacy_exports_lazy(self) -> None:
        self.run_isolated(
            "import sys\n"
            "import auto_research.product as product\n"
            "assert 'auto_research.product.evidence_v12_export' not in sys.modules\n"
            "assert product.EXPECTED_DISTRIBUTION_SCHEMA == 1\n"
            "assert 'auto_research.product.evidence_v12_export' not in sys.modules\n"
            "assert callable(product.plan_evidence_v12_export)\n"
            "assert 'auto_research.product.evidence_v12_export' in sys.modules\n"
        )


if __name__ == "__main__":
    unittest.main()
