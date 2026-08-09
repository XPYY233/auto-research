from __future__ import annotations

import json
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
        self.assertEqual(contract.macos_version, "0.6.1-preview.1")
        self.assertEqual(contract.windows_version, "0.4.0-internal.1")
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
            relative = "web/app.js"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("changed", encoding="utf-8")
            value["web_assets"] = {relative: "0" * 64}
            contract = validate_release_contract(value)
            with self.assertRaisesRegex(ReleaseContractError, "已变化"):
                verify_web_asset_hashes(contract, root)


if __name__ == "__main__":
    unittest.main()
