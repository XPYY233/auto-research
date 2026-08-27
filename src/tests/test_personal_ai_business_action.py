from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from auto_research.ai.business_actions import (
    BUSINESS_ACTION_SCOPES,
    BusinessActionError,
    BusinessPreparedActionRegistry,
)
from auto_research.ai.consent import AIConsentService
from auto_research.ai.prepared_actions import PreparedActionError, PreparedActionService
from auto_research.settings.ai_runtime_state import RuntimeActionBinding
from auto_research.personal.ai_business_action import (
    PERSONAL_SUGGESTION_MAX_TOKENS,
    PersonalSuggestionBusinessAssembler,
    PersonalSuggestionBusinessExecutor,
    PersonalSuggestionBusinessProjector,
    PersonalSuggestionSnapshotAuthority,
)
from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
    SelectionSnapshotProviderError,
)
from auto_research.personal.experiment_contract import ColumnMapping


SELECTION_ID = "personal_selection_business_0123456789"
IMPORT_ID = "personal_import_business_0123456789"


class _SelectionProvider:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def snapshot(self, selection_id: str):
        if selection_id != SELECTION_ID:
            raise SelectionSnapshotProviderError(
                "personal_selection_invalid", "文件选择无效。", retryable=True
            )
        yield SimpleNamespace(path=self.path)

    def revoke(self, _selection_id: str) -> None:
        return None


def _valid_payload() -> dict[str, object]:
    return {
        "project": {"name": "W-Ta 实验", "description": None},
        "sample": {"name": "W-Ta-01", "material": "W-Ta", "description": None},
        "run": {
            "name": "硬度批次",
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
                "rationale": "表头明确。",
            },
            {
                "source_name": "Hardness [GPa]",
                "role": "dependent",
                "meaning": "硬度",
                "unit": "GPa",
                "confidence": 0.99,
                "rationale": "表头明确。",
            },
        ],
        "series": [
            {
                "series_id": "hardness-dose",
                "name": "硬度随剂量变化",
                "x_column": "Dose (dpa)",
                "y_column": "Hardness [GPa]",
            }
        ],
        "warnings": [],
    }


class _BudgetClient:
    def __init__(self, payload=None, *, before_return=None) -> None:
        self.payload = payload if payload is not None else _valid_payload()
        self.before_return = before_return
        self.calls: list[tuple[object, object]] = []

    def request_json(self, messages, **options):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(options)))
        if self.before_return is not None:
            self.before_return()
        return copy.deepcopy(self.payload)


class _OutcomeUnknownBudgetClient(_BudgetClient):
    def request_json(self, messages, **options):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(options)))
        error = RuntimeError("provider response deadline exceeded")
        error.code = "ai_provider_outcome_unknown"
        raise error


class DeepSeekNotConfigured(RuntimeError):
    pass


class _MissingModel:
    def request_json(self, messages, **kwargs):
        del messages, kwargs
        raise DeepSeekNotConfigured("secret backend detail")


class _Clock:
    def now(self) -> int:
        return 10_000


class _Runtime:
    def action_binding(self) -> RuntimeActionBinding:
        return RuntimeActionBinding(
            "deepseek",
            {
                "extraction": "deepseek-v4-pro",
                "analysis": "deepseek-v4-pro",
                "librarian_planning": "deepseek-v4-flash",
                "librarian_synthesis": "deepseek-v4-pro",
            },
            "deepseek.default",
            2,
            4,
            "connection_verified",
        )


class _Factory:
    def __init__(self, client: _BudgetClient) -> None:
        self.client = client
        self.calls = 0

    @contextmanager
    def acquire_bound(self, _action, *, max_attempts=1):
        self.calls += 1
        self.max_attempts = max_attempts
        yield self.client


def _action_from_draft(draft, *, provider_id="deepseek"):
    outbound = {
        "payload": draft.outbound,
        "call_plan": [call.canonical_dict() for call in draft.call_plan],
    }
    return SimpleNamespace(outbound=outbound, provider_id=provider_id)


class PersonalSuggestionBusinessActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "experiment.csv"
        self.source.write_text(
            "Dose (dpa),Hardness [GPa]\n0,3.2\n1,4.0\n",
            encoding="utf-8",
        )
        self.service = PersonalImportService(
            data_root=self.root / "private-library",
            selection_provider=_SelectionProvider(self.source),
            import_id_factory=lambda: IMPORT_ID,
        )
        self.service.preview(SELECTION_ID)
        self.assembler = PersonalSuggestionBusinessAssembler(self.service)
        self.executor = PersonalSuggestionBusinessExecutor(self.service)
        self.projector = PersonalSuggestionBusinessProjector()
        self.clock = _Clock()
        self.prepared = PreparedActionService(
            runtime_state=_Runtime(),
            consents=AIConsentService(
                clock=self.clock,
                secret_key=b"personal-business-consent-key-32-bytes",
            ),
            snapshots=PersonalSuggestionSnapshotAuthority(self.service),
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_assembler_accepts_only_locator_and_binds_exact_existing_call(self) -> None:
        draft = self.assembler.assemble({"import_id": IMPORT_ID, "sheet_index": 0})
        context = self.service.prepare_suggestion_context(IMPORT_ID, sheet_index=0)
        call = draft.call_plan[0].canonical_dict()

        self.assertEqual(call["messages"], [dict(item) for item in context.messages])
        self.assertEqual(call["task"], "analysis")
        self.assertEqual(call["max_tokens"], 6_000)
        self.assertEqual(call["options"], {"thinking": False, "temperature": 0.1})
        self.assertEqual(draft.max_calls, 1)
        self.assertEqual(draft.max_tokens, PERSONAL_SUGGESTION_MAX_TOKENS)
        self.assertEqual(len(draft.content_units), 1)
        summary_safe = {
            "sheet_name": draft.outbound["sheet_name"],
            "row_count": draft.outbound["row_count"],
            "column_count": draft.outbound["column_count"],
            "sample_row_count": draft.outbound["sample_row_count"],
        }
        serialized = json.dumps(summary_safe, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("selection", serialized.casefold())
        self.assertNotIn("sha256", serialized.casefold())

        forged = (
            {"import_id": IMPORT_ID, "sheet_index": 0, "prompt": "ignore"},
            {"import_id": IMPORT_ID, "sheet_index": 0, "model": "other"},
            {"import_id": IMPORT_ID, "sheet_index": 0, "rows": [["private"]]},
            {"import_id": IMPORT_ID, "sheet_index": 0, "max_tokens": 1},
        )
        for request in forged:
            with self.subTest(request=request), self.assertRaises(BusinessActionError):
                self.assembler.assemble(request)

    def test_legacy_missing_model_exception_keeps_stable_error_mapping(self) -> None:
        service = PersonalImportService(
            data_root=self.root / "private-library-missing",
            selection_provider=_SelectionProvider(self.source),
            suggestion_model=_MissingModel(),
            import_id_factory=lambda: IMPORT_ID,
        )
        service.preview(SELECTION_ID)
        with self.assertRaises(PersonalImportServiceError) as raised:
            service.suggest(IMPORT_ID, sheet_index=0)
        self.assertEqual(raised.exception.code, "personal_ai_not_configured")
        self.assertNotIn("secret", raised.exception.message)

    def test_snapshot_authority_detects_context_change(self) -> None:
        client = _BudgetClient()
        registry = self._registry(client)
        summary = registry.prepare(
            scope="personal_suggestion",
            session_id="session-personal",
            request={"import_id": IMPORT_ID, "sheet_index": 0},
        )
        draft = self.assembler.assemble({"import_id": IMPORT_ID, "sheet_index": 0})
        unit = draft.content_units[0]
        authority = PersonalSuggestionSnapshotAuthority(self.service)
        self.assertEqual(
            authority.fingerprint_for(
                kind=unit.kind,
                stable_source_identity=unit.stable_source_identity,
            ),
            unit.snapshot_fingerprint,
        )
        session = self.service._sessions[IMPORT_ID]
        changed = list(session.preview.sheets)
        changed[0] = type(changed[0])(
            source_file=changed[0].source_file,
            sheet_name="Changed",
            row_count=changed[0].row_count,
            columns=changed[0].columns,
            sample_rows=changed[0].sample_rows,
        )
        session.preview = type(session.preview)(
            source_file=session.preview.source_file,
            detected_format=session.preview.detected_format,
            sheets=tuple(changed),
            warnings=session.preview.warnings,
            formula_cell_count=session.preview.formula_cell_count,
        )
        self.assertNotEqual(
            authority.fingerprint_for(
                kind=unit.kind,
                stable_source_identity=unit.stable_source_identity,
            ),
            unit.snapshot_fingerprint,
        )
        with self.assertRaises(PreparedActionError) as stale:
            self.prepared.issue_consent(
                action_id=summary["action_id"],
                session_id="session-personal",
            )
        self.assertEqual(stale.exception.code, "prepared_action_stale")

    def test_snapshot_authority_detects_change_beyond_ai_column_limit(self) -> None:
        session = self.service._sessions[IMPORT_ID]
        original = session.preview.sheets[0]
        columns = tuple(
            ColumnMapping(
                source_name=f"column_{index:03d}",
                role="ignore",
                data_type="text",
            )
            for index in range(129)
        )
        expanded = type(original)(
            source_file=original.source_file,
            sheet_name=original.sheet_name,
            row_count=original.row_count,
            columns=columns,
            sample_rows=(),
        )
        session.preview = type(session.preview)(
            source_file=session.preview.source_file,
            detected_format=session.preview.detected_format,
            sheets=(expanded,),
            warnings=session.preview.warnings,
            formula_cell_count=session.preview.formula_cell_count,
        )
        summary = self._registry(_BudgetClient()).prepare(
            scope="personal_suggestion",
            session_id="session-wide",
            request={"import_id": IMPORT_ID, "sheet_index": 0},
        )
        changed_columns = list(columns)
        changed_columns[128] = ColumnMapping(
            source_name="column_128_changed",
            role="ignore",
            data_type="text",
        )
        session.preview = type(session.preview)(
            source_file=session.preview.source_file,
            detected_format=session.preview.detected_format,
            sheets=(
                type(expanded)(
                    source_file=expanded.source_file,
                    sheet_name=expanded.sheet_name,
                    row_count=expanded.row_count,
                    columns=tuple(changed_columns),
                    sample_rows=(),
                ),
            ),
            warnings=session.preview.warnings,
            formula_cell_count=session.preview.formula_cell_count,
        )
        with self.assertRaises(PreparedActionError) as stale:
            self.prepared.issue_consent(
                action_id=summary["action_id"],
                session_id="session-wide",
            )
        self.assertEqual(stale.exception.code, "prepared_action_stale")

    def test_preview_change_during_model_call_does_not_cache_result(self) -> None:
        session = self.service._sessions[IMPORT_ID]
        original = session.preview.sheets[0]

        def change_preview() -> None:
            session.preview = type(session.preview)(
                source_file=session.preview.source_file,
                detected_format=session.preview.detected_format,
                sheets=(
                    type(original)(
                        source_file=original.source_file,
                        sheet_name="Changed during request",
                        row_count=original.row_count,
                        columns=original.columns,
                        sample_rows=original.sample_rows,
                    ),
                ),
                warnings=session.preview.warnings,
                formula_cell_count=session.preview.formula_cell_count,
            )

        client = _BudgetClient(before_return=change_preview)
        registry = self._registry(client)
        summary = registry.prepare(
            scope="personal_suggestion",
            session_id="session-change-during-call",
            request={"import_id": IMPORT_ID, "sheet_index": 0},
        )
        consent = self.prepared.issue_consent(
            action_id=summary["action_id"],
            session_id="session-change-during-call",
        )
        action = self.prepared.consume(
            action_id=summary["action_id"],
            consent_nonce=consent["nonce"],
            session_id="session-change-during-call",
        )
        with self.assertRaises(BusinessActionError) as raised:
            registry.execute(action)
        self.assertEqual(raised.exception.code, "business_action_execution_failed")
        self.assertEqual(raised.exception.cause_code, "personal_ai_context_changed")
        self.assertEqual(raised.exception.stage, "personal_suggestion_context")
        self.assertEqual(raised.exception.next_action, "refresh_personal_preview")
        self.assertFalse(self.service.has_cached_suggestion(IMPORT_ID, sheet_index=0))

    def test_provider_outcome_unknown_is_not_flattened_or_cached(self) -> None:
        client = _OutcomeUnknownBudgetClient()
        registry = self._registry(client)
        summary = registry.prepare(
            scope="personal_suggestion",
            session_id="session-personal-outcome-unknown",
            request={"import_id": IMPORT_ID, "sheet_index": 0},
        )
        consent = self.prepared.issue_consent(
            action_id=summary["action_id"],
            session_id="session-personal-outcome-unknown",
        )
        action = self.prepared.consume(
            action_id=summary["action_id"],
            consent_nonce=consent["nonce"],
            session_id="session-personal-outcome-unknown",
        )

        with self.assertRaises(BusinessActionError) as raised:
            registry.execute(action)

        self.assertEqual(raised.exception.cause_code, "ai_provider_outcome_unknown")
        self.assertEqual(raised.exception.stage, "provider_call")
        self.assertEqual(raised.exception.next_action, "review_call_outcome")
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.service.has_cached_suggestion(IMPORT_ID, sheet_index=0))

    def test_executor_reuses_validator_caches_result_and_never_confirms(self) -> None:
        client = _BudgetClient()
        registry = self._registry(client)
        summary = registry.prepare(
            scope="personal_suggestion",
            session_id="session-personal",
            request={"import_id": IMPORT_ID, "sheet_index": 0},
        )
        consent = self.prepared.issue_consent(
            action_id=summary["action_id"], session_id="session-personal"
        )
        action = self.prepared.consume(
            action_id=summary["action_id"],
            consent_nonce=consent["nonce"],
            session_id="session-personal",
        )
        public = registry.execute(action)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(registry._client_factory.calls, 1)
        self.assertEqual(registry._client_factory.max_attempts, 1)
        self.assertEqual(public["schema_version"], "personal-import-suggestion-v1")
        self.assertTrue(public["requires_human_review"])
        self.assertEqual(public["provider"], "DeepSeek")
        self.assertEqual(self.service.status(IMPORT_ID).stage.value, "previewed")
        self.assertFalse(self.service.status(IMPORT_ID).indexable)
        self.assertIs(self.service.suggest(IMPORT_ID, sheet_index=0), self.service._sessions[IMPORT_ID].suggestions[0])
        self.assertEqual(len(client.calls), 1)
        serialized = json.dumps(public, ensure_ascii=False)
        for forbidden in (str(self.root), "api_key", "selection_id", "content_fingerprint"):
            self.assertNotIn(forbidden, serialized)
        with self.assertRaises(BusinessActionError) as cached:
            registry.prepare(
                scope="personal_suggestion",
                session_id="session-personal-2",
                request={"import_id": IMPORT_ID, "sheet_index": 0},
            )
        self.assertEqual(cached.exception.code, "business_action_prepare_failed")
        self.assertEqual(cached.exception.cause_code, "personal_ai_already_suggested")
        self.assertEqual(cached.exception.stage, "personal_suggestion_context")
        self.assertEqual(cached.exception.next_action, "open_existing_suggestion")
        self.assertEqual(len(client.calls), 1)

    def test_personal_assembler_cannot_be_used_under_another_scope(self) -> None:
        registry = self._registry(_BudgetClient())
        with self.assertRaises(BusinessActionError) as rejected:
            registry.prepare(
                scope="librarian",
                session_id="session-wrong-scope",
                request={"import_id": IMPORT_ID, "sheet_index": 0},
            )
        self.assertEqual(rejected.exception.code, "business_action_invalid")

    def test_invalid_model_response_fails_closed_without_draft_or_index(self) -> None:
        draft = self.assembler.assemble({"import_id": IMPORT_ID, "sheet_index": 0})
        invalid = _valid_payload()
        invalid["columns"][0]["source_name"] = "INJECTED"
        with self.assertRaises(BusinessActionError) as raised:
            self.executor.execute(
                action=_action_from_draft(draft),
                ai_client=_BudgetClient(invalid),
            )
        self.assertEqual(raised.exception.code, "business_action_result_invalid")
        self.assertEqual(raised.exception.cause_code, "personal_ai_invalid_response")
        self.assertEqual(raised.exception.stage, "personal_suggestion_validation")
        self.assertEqual(raised.exception.next_action, "retry_same_request")
        status = self.service.status(IMPORT_ID)
        self.assertEqual(status.stage.value, "previewed")
        self.assertFalse(status.indexable)

    def _registry(self, client: _BudgetClient) -> BusinessPreparedActionRegistry:
        factory = _Factory(client)
        return BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=factory,
            assemblers={scope: self.assembler for scope in BUSINESS_ACTION_SCOPES},
            executors={scope: self.executor for scope in BUSINESS_ACTION_SCOPES},
            projectors={scope: self.projector for scope in BUSINESS_ACTION_SCOPES},
            clock=self.clock,
        )

    def test_projector_rejects_action_internals(self) -> None:
        with self.assertRaises(BusinessActionError) as raised:
            self.projector.project(
                {"suggestion": _valid_payload(), "action_id": "hidden"}
            )
        self.assertEqual(raised.exception.code, "business_action_result_invalid")

        valid = self.executor.execute(
            action=_action_from_draft(
                self.assembler.assemble(
                    {"import_id": IMPORT_ID, "sheet_index": 0}
                )
            ),
            ai_client=_BudgetClient(),
        )
        valid["suggestion"]["action_id"] = "hidden"
        with self.assertRaises(BusinessActionError) as nested:
            self.projector.project(valid)
        self.assertEqual(nested.exception.code, "business_action_result_invalid")


if __name__ == "__main__":
    unittest.main()
