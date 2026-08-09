from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
    SelectionSnapshotProviderError,
)


SELECTION_ID = "personal_selection_ai0123456789abcdef"
IMPORT_ID = "personal_import_ai0123456789abcdef"


class _Provider:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.revoked: list[str] = []

    @contextmanager
    def snapshot(self, selection_id: str):
        if selection_id != SELECTION_ID:
            raise SelectionSnapshotProviderError(
                "personal_selection_invalid",
                "文件选择无效。",
                retryable=True,
            )
        yield SimpleNamespace(path=self.path)

    def revoke(self, selection_id: str) -> None:
        self.revoked.append(selection_id)


def _valid_response() -> dict[str, Any]:
    return {
        "project": {"name": "W-Ta 辐照硬度实验", "description": None},
        "sample": {"name": "W-Ta 样品", "material": "W-Ta", "description": None},
        "run": {
            "name": "硬度-剂量批次",
            "method": "纳米压痕",
            "conditions": {},
            "user_note": None,
        },
        "columns": [
            {
                "source_name": "Dose (dpa)",
                "role": "independent",
                "meaning": "辐照剂量",
                "unit": "dpa",
                "confidence": 0.98,
                "rationale": "表头明确给出剂量与单位。",
            },
            {
                "source_name": "Hardness [GPa]",
                "role": "dependent",
                "meaning": "硬度",
                "unit": "GPa",
                "confidence": 0.99,
                "rationale": "表头明确给出硬度与单位。",
            },
        ],
        "series": [
            {
                "series_id": "hardness-dose",
                "name": "硬度随辐照剂量变化",
                "x_column": "Dose (dpa)",
                "y_column": "Hardness [GPa]",
                "description": "剂量为自变量，硬度为因变量。",
            }
        ],
        "warnings": ["请检查样品名称是否与实验记录一致。"],
    }


class _Model:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or _valid_response()
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def request_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.payload


class _BlockingModel(_Model):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def request_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        self.started.set()
        self.release.wait(timeout=2)
        return self.payload


class PersonalImportSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "experiment.csv"
        self.source.write_text(
            "Dose (dpa),Hardness [GPa]\n"
            + "\n".join(f"{index},{3.0 + index / 10}" for index in range(12))
            + "\n",
            encoding="utf-8",
        )
        self.provider = _Provider(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _service(self, model=None) -> PersonalImportService:
        return PersonalImportService(
            data_root=self.root / "private-library",
            selection_provider=self.provider,
            suggestion_model=model,
            import_id_factory=lambda: IMPORT_ID,
        )

    def test_bounded_suggestion_is_path_free_and_does_not_confirm(self) -> None:
        model = _Model()
        service = self._service(model)
        service.preview(SELECTION_ID)
        suggestion = service.suggest(IMPORT_ID, sheet_index=0)
        public = suggestion.public_dict()
        serialized = json.dumps(public, ensure_ascii=False)
        prompt = json.dumps(model.calls[0][0], ensure_ascii=False)
        user_prompt = model.calls[0][0][1]["content"]

        self.assertEqual(public["schema_version"], "personal-import-suggestion-v1")
        self.assertTrue(public["requires_human_review"])
        self.assertEqual(public["provider"], "DeepSeek")
        self.assertEqual(service.status(IMPORT_ID).stage.value, "previewed")
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(str(self.root), prompt)
        self.assertNotIn("11,4.1", prompt)
        self.assertIn('"sample_rows"', user_prompt)
        self.assertIn('"Dose (dpa)":"0"', user_prompt)
        self.assertIn('"Hardness [GPa]":"3.0"', user_prompt)
        self.assertNotIn('"sample_values"', user_prompt)
        self.assertEqual(len(model.calls), 1)
        self.assertIs(service.suggest(IMPORT_ID, sheet_index=0), suggestion)
        self.assertEqual(len(model.calls), 1)

    def test_model_cannot_add_unknown_columns_or_series_references(self) -> None:
        payload = _valid_response()
        payload["columns"][0]["source_name"] = "INJECTED"
        service = self._service(_Model(payload))
        service.preview(SELECTION_ID)
        with self.assertRaises(PersonalImportServiceError) as raised:
            service.suggest(IMPORT_ID, sheet_index=0)
        self.assertEqual(raised.exception.code, "personal_ai_invalid_response")
        self.assertEqual(service.status(IMPORT_ID).stage.value, "previewed")

    def test_ignore_column_empty_conditions_and_empty_series_remain_valid(self) -> None:
        payload = _valid_response()
        payload["run"]["conditions"] = {}
        payload["columns"][0].update(
            {"role": "ignore", "meaning": None, "unit": None}
        )
        payload["series"] = []
        service = self._service(_Model(payload))
        service.preview(SELECTION_ID)
        suggestion = service.suggest(IMPORT_ID, sheet_index=0)
        self.assertEqual(suggestion.columns[0].role, "ignore")
        self.assertIsNone(suggestion.columns[0].meaning)
        self.assertEqual(suggestion.run["conditions"], {})
        self.assertEqual(suggestion.series, ())

    def test_missing_model_does_not_block_local_preview(self) -> None:
        service = self._service()
        preview = service.preview(SELECTION_ID)
        self.assertEqual(preview.status.stage.value, "previewed")
        with self.assertRaises(PersonalImportServiceError) as raised:
            service.suggest(IMPORT_ID, sheet_index=0)
        self.assertEqual(raised.exception.code, "personal_ai_not_configured")
        self.assertFalse(raised.exception.retryable)

    def test_one_import_has_only_one_paid_suggestion_in_flight(self) -> None:
        model = _BlockingModel()
        service = self._service(model)
        service.preview(SELECTION_ID)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(service.suggest, IMPORT_ID, sheet_index=0)
            self.assertTrue(model.started.wait(timeout=1))
            with self.assertRaises(PersonalImportServiceError) as raised:
                service.suggest(IMPORT_ID, sheet_index=0)
            self.assertEqual(raised.exception.code, "personal_ai_busy")
            model.release.set()
            self.assertEqual(first.result().sheet_index, 0)
        self.assertEqual(len(model.calls), 1)


if __name__ == "__main__":
    unittest.main()
