from __future__ import annotations

import unittest
from pathlib import Path

from auto_research.personal.public_projection import (
    project_personal_renderer_payload,
)


class PersonalPublicProjectionTests(unittest.TestCase):
    def test_recursively_removes_private_file_identity_and_paths(self) -> None:
        payload = {
            "schema_version": "personal-import-preview-v1",
            "status": {
                "import_id": "personal_import_0123456789abcdef",
                "revision": 4,
                "source_file": {
                    "file_id": "private-file",
                    "source_file_id": "private-source-file",
                    "sha256": "a" * 64,
                    "path": "/private/input.csv",
                    "relative_path": "files/input.csv",
                    "original_name": "input.csv",
                },
            },
            "preview": {
                "sheets": [
                    {
                        "sheet_name": "Sheet1",
                        "columns": [
                            {
                                "source_name": "Dose",
                                "series_id": "dose-series",
                                "database_path": "/private/library.sqlite",
                            }
                        ],
                    }
                ]
            },
            "active_fingerprint": "f" * 64,
        }

        projected = project_personal_renderer_payload(payload)

        self.assertEqual(
            projected["status"]["import_id"],
            "personal_import_0123456789abcdef",
        )
        self.assertEqual(projected["status"]["revision"], 4)
        self.assertEqual(
            projected["preview"]["sheets"][0]["columns"][0]["source_name"],
            "Dose",
        )
        self.assertEqual(
            projected["preview"]["sheets"][0]["columns"][0]["series_id"],
            "dose-series",
        )
        serialized = repr(projected).casefold()
        for forbidden in (
            "sha256",
            "file_id",
            "source_file_id",
            "active_fingerprint",
            "path",
            "/private",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_projection_is_detached_and_rejects_non_json_values(self) -> None:
        payload = {"sheets": [{"columns": ["Dose"]}]}
        projected = project_personal_renderer_payload(payload)
        payload["sheets"][0]["columns"].append("Hardness")
        self.assertEqual(projected, {"sheets": [{"columns": ["Dose"]}]})

        with self.assertRaises(TypeError):
            project_personal_renderer_payload({"unexpected": Path("private.csv")})


if __name__ == "__main__":
    unittest.main()
