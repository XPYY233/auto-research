from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from auto_research.release_contract import (
    ReleaseContractError,
    load_release_contract,
    validate_release_contract,
    verify_web_asset_hashes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ReleaseContractTests(unittest.TestCase):
    def test_repository_contract_is_valid_and_assets_match(self) -> None:
        contract = load_release_contract(PROJECT_ROOT / "config" / "release-contract.json")
        self.assertEqual(contract.macos_version, "0.8.0-preview.1")
        self.assertEqual(contract.windows_version, "0.8.0-internal.1")
        self.assertEqual(contract.official_package_version, "0.2.0-preview.1")
        verify_web_asset_hashes(contract, PROJECT_ROOT)

    def test_windows_cannot_claim_installer_ready_before_real_acceptance(self) -> None:
        value = json.loads((PROJECT_ROOT / "config" / "release-contract.json").read_text())
        value["desktop"]["windows"]["installer_ready"] = True
        with self.assertRaisesRegex(ReleaseContractError, "installer_ready"):
            validate_release_contract(value)

    def test_user_transfer_kinds_cannot_be_mixed(self) -> None:
        value = json.loads((PROJECT_ROOT / "config" / "release-contract.json").read_text())
        value["packages"]["transfer"]["package_kinds"].append("mixed")
        with self.assertRaisesRegex(ReleaseContractError, "严格分离"):
            validate_release_contract(value)

    def test_asset_hash_drift_fails_closed(self) -> None:
        value = json.loads((PROJECT_ROOT / "config" / "release-contract.json").read_text())
        with tempfile.TemporaryDirectory(prefix="release-contract-test-") as temporary:
            root = Path(temporary)
            for relative in value["web_assets"]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("expected", encoding="utf-8")
                value["web_assets"][relative] = hashlib.sha256(
                    b"expected"
                ).hexdigest()
            contract = validate_release_contract(value)
            (root / "src/auto_research/evidence/web/app.js").write_text(
                "changed", encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseContractError, "已变化"):
                verify_web_asset_hashes(contract, root)

    def test_package_center_asset_cannot_be_omitted(self) -> None:
        value = json.loads((PROJECT_ROOT / "config" / "release-contract.json").read_text())
        value["web_assets"].pop(
            "src/auto_research/evidence/web/package_center.js"
        )
        with self.assertRaisesRegex(ReleaseContractError, "全部共享前端资产"):
            validate_release_contract(value)

    def test_ai_consent_asset_cannot_be_omitted(self) -> None:
        value = json.loads((PROJECT_ROOT / "config" / "release-contract.json").read_text())
        value["web_assets"].pop("src/auto_research/evidence/web/ai_consent.js", None)
        with self.assertRaisesRegex(ReleaseContractError, "全部共享前端资产"):
            validate_release_contract(value)

    def test_workbench_assets_cannot_be_omitted(self) -> None:
        for relative in (
            "src/auto_research/evidence/web/workbench.css",
            "src/auto_research/evidence/web/workbench.js",
        ):
            with self.subTest(relative=relative):
                value = json.loads(
                    (PROJECT_ROOT / "config" / "release-contract.json").read_text()
                )
                value["web_assets"].pop(relative, None)
                with self.assertRaisesRegex(ReleaseContractError, "全部共享前端资产"):
                    validate_release_contract(value)


if __name__ == "__main__":
    unittest.main()
