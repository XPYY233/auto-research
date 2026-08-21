from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from auto_research.personal.ai_import_suggestions import (
    PersonalImportSuggestion,
    PersonalImportSuggestionError,
    PersonalSuggestionModel,
    build_suggestion_messages,
    validate_suggestion_payload,
)

from auto_research.personal.experiment_contract import (
    ColumnMapping,
    MeasurementSeriesDraft,
    PersonalExperimentDraft,
    TabularImportPreview,
)
from auto_research.personal.private_repository import (
    PrivateExperimentRepository,
    PrivateOperationResult,
    PrivateProject,
    PrivateRepositoryError,
    PrivateSample,
)
from auto_research.personal.search_source import (
    PrivateRepositorySearchSource,
    PrivateSearchSnapshot,
)
from auto_research.personal.tabular_preview import (
    PreviewLimits,
    TabularFilePreview,
    UnsafeTabularFileError,
    preview_tabular_file,
)


DEFAULT_IMPORT_SESSION_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_IMPORT_SESSIONS = 32
_IMPORT_ID_RE = re.compile(r"^personal_import_[A-Za-z0-9_-]{16,96}$")
_STAGED_FILENAME_RE = re.compile(
    r"^personal_import_[A-Za-z0-9_-]{16,96}-[A-Za-z0-9_-]{8,64}\.(?:csv|tsv|xlsx)$"
)


class SelectionSnapshot(Protocol):
    path: Path


class SelectionSnapshotProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class SelectionSnapshotProvider(Protocol):
    def snapshot(self, selection_id: str) -> AbstractContextManager[SelectionSnapshot]: ...

    def revoke(self, selection_id: str) -> None: ...


class PersonalImportStage(str, Enum):
    PREVIEWED = "previewed"
    DRAFT_SAVED = "draft_saved"
    INDEXABLE = "indexable"


class PersonalImportServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = _safe_error_details(details or {})

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": "personal-import-error-v1",
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            value["details"] = dict(self.details)
        return value


@dataclass(frozen=True)
class PersonalImportStatus:
    import_id: str
    stage: PersonalImportStage
    source_file: Mapping[str, Any]
    confirmation_state: str
    indexable: bool
    revision: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-import-status-v1",
            "import_id": self.import_id,
            "stage": self.stage.value,
            "source_file": dict(self.source_file),
            "confirmation_state": self.confirmation_state,
            "indexable": self.indexable,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class PersonalImportPreview:
    status: PersonalImportStatus
    preview: TabularFilePreview

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-import-preview-v1",
            "status": self.status.public_dict(),
            "preview": self.preview.as_dict(),
        }


@dataclass(frozen=True)
class PersonalSuggestionContext:
    """Backend-only immutable input to one reviewed AI suggestion call."""

    import_id: str
    sheet_index: int
    messages: tuple[Mapping[str, str], ...]
    prompt_warnings: tuple[str, ...]
    # Keep the local stale authority separate from the deliberately bounded
    # outbound prompt.  Future preview fields that affect suggestions must be
    # added to the preview fingerprint envelope in _suggestion_context_locked.
    preview_fingerprint: str
    outbound_fingerprint: str
    byte_count: int
    sheet_name: str
    row_count: int
    column_count: int
    sample_row_count: int


@dataclass
class _ImportSession:
    import_id: str
    selection_id: str
    preview: TabularFilePreview
    project_id: str
    sample_id: str
    draft_id: str
    created_at: float
    expires_at: float
    stage: PersonalImportStage = PersonalImportStage.PREVIEWED
    revision: int | None = None
    draft: PersonalExperimentDraft | None = None
    pending_draft: PersonalExperimentDraft | None = None
    project: PrivateProject | None = None
    sample: PrivateSample | None = None
    project_created: bool = False
    sample_created: bool = False
    file_registered: bool = False
    staged_path: Path | None = None
    suggestions: dict[int, PersonalImportSuggestion] | None = None
    reviewed_payload_fingerprint: str | None = None


Clock = Callable[[], float]
ImportIdFactory = Callable[[], str]
RepositoryFactory = Callable[[Path], PrivateExperimentRepository]
Previewer = Callable[..., TabularFilePreview]


_SAFE_DETAIL_KEYS = {
    "entity_type",
    "field",
    "current_state",
    "required_state",
    "issue_codes",
    "schema_version",
    "operation",
    "expected_revision",
    "actual_revision",
    "stage",
}
_RETRYABLE_REPOSITORY_CODES = {
    "PRIVATE_DB_UNAVAILABLE",
    "PRIVATE_DB_READ_FAILED",
    "PRIVATE_DB_WRITE_FAILED",
    "SOURCE_FILE_UNAVAILABLE",
}


def _safe_error_details(details: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in details.items():
        if key not in _SAFE_DETAIL_KEYS:
            continue
        if isinstance(value, bool) or value is None:
            output[key] = value
        elif isinstance(value, int):
            output[key] = value
        elif isinstance(value, (list, tuple)):
            output[key] = [str(item)[:120] for item in value[:20]]
        else:
            output[key] = str(value)[:240]
    return output


def _service_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Mapping[str, Any] | None = None,
) -> PersonalImportServiceError:
    return PersonalImportServiceError(
        code,
        message,
        retryable=retryable,
        details=details,
    )


def _translate_selection(error: SelectionSnapshotProviderError) -> PersonalImportServiceError:
    return _service_error(error.code, error.message, retryable=error.retryable)


def _translate_repository(error: PrivateRepositoryError) -> PersonalImportServiceError:
    return _service_error(
        error.code,
        error.safe_message,
        retryable=error.code in _RETRYABLE_REPOSITORY_CODES,
        details=error.details,
    )


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _service_error(
            "personal_request_invalid",
            "个人实验请求格式无效。",
            details={"field": field},
        )
    return dict(value)


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    field: str,
) -> None:
    keys = set(value)
    if not required <= keys or keys - required - optional:
        raise _service_error(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            details={"field": field},
        )


def _required_text(value: Any, field: str, *, limit: int = 500) -> str:
    if not isinstance(value, str):
        raise _service_error(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            details={"field": field},
        )
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > limit:
        raise _service_error(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            details={"field": field},
        )
    return cleaned


def _optional_text(value: Any, field: str, *, limit: int = 2_000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _service_error(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            details={"field": field},
        )
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise _service_error(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            details={"field": field},
        )
    return cleaned or None


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise _service_error(
            "personal_confirmation_flag_required",
            "请明确确认每列的角色、含义和单位。",
            details={"field": field},
        )
    return value


class PersonalImportService:
    """macOS orchestration over the frozen, platform-neutral personal core."""

    def __init__(
        self,
        *,
        data_root: Path | str,
        selection_provider: SelectionSnapshotProvider,
        repository: PrivateExperimentRepository | None = None,
        repository_factory: RepositoryFactory = PrivateExperimentRepository,
        previewer: Previewer = preview_tabular_file,
        preview_limits: PreviewLimits | None = None,
        import_id_factory: ImportIdFactory | None = None,
        clock: Clock = time.monotonic,
        session_ttl_seconds: float = DEFAULT_IMPORT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_IMPORT_SESSIONS,
        suggestion_model: PersonalSuggestionModel | None = None,
        suggestion_provider_label: str = "DeepSeek",
    ) -> None:
        if data_root is None or not str(data_root).strip():
            raise ValueError("personal data root is required")
        if not isinstance(session_ttl_seconds, (int, float)) or not 300 <= session_ttl_seconds <= 86_400:
            raise ValueError("personal import session TTL is invalid")
        if not isinstance(max_sessions, int) or not 1 <= max_sessions <= 128:
            raise ValueError("personal import session capacity is invalid")
        self.data_root = Path(data_root).expanduser().absolute()
        self.selection_provider = selection_provider
        self._repository_value = repository
        self._repository_factory = repository_factory
        self._previewer = previewer
        self._preview_limits = preview_limits or PreviewLimits()
        self._import_id_factory = import_id_factory or self._new_import_id
        self._clock = clock
        self._session_ttl_seconds = float(session_ttl_seconds)
        self._max_sessions = max_sessions
        self._suggestion_model = suggestion_model
        self._suggestion_provider_label = " ".join(
            str(suggestion_provider_label or "DeepSeek").split()
        )[:80] or "DeepSeek"
        self._sessions: dict[str, _ImportSession] = {}
        self._suggestions_in_flight: set[tuple[str, int]] = set()
        self._lock = threading.RLock()
        self._cleanup_orphaned_staging()

    @staticmethod
    def _new_import_id() -> str:
        return f"personal_import_{secrets.token_urlsafe(24)}"

    def preview(self, selection_id: str) -> PersonalImportPreview:
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._sessions) >= self._max_sessions:
                raise _service_error(
                    "personal_import_capacity",
                    "待处理的个人实验导入过多，请稍后再试。",
                    retryable=True,
                )
            staged_path: Path | None = None
            try:
                with self.selection_provider.snapshot(selection_id) as selected:
                    preview = self._previewer(
                        selected.path,
                        limits=self._preview_limits,
                    )
                    import_id = self._validated_new_import_id()
                    staged_path = self._stage_snapshot(
                        selected.path,
                        preview=preview,
                        import_id=import_id,
                    )
            except SelectionSnapshotProviderError as exc:
                raise _translate_selection(exc) from exc
            except UnsafeTabularFileError as exc:
                raise _service_error(
                    "personal_preview_rejected",
                    "该表格不能在安全限制内预览，请检查文件格式。",
                    retryable=False,
                ) from exc
            except (OSError, ValueError) as exc:
                raise _service_error(
                    "personal_preview_failed",
                    "表格预览暂时无法完成，请重新选择。",
                    retryable=True,
                ) from exc
            except PersonalImportServiceError:
                if staged_path is not None:
                    self._remove_staged_path(staged_path)
                raise
            token = import_id.removeprefix("personal_import_")
            session = _ImportSession(
                import_id=import_id,
                selection_id=selection_id,
                preview=preview,
                project_id=f"project-{token}",
                sample_id=f"sample-{token}",
                draft_id=f"run-{token}",
                created_at=now,
                expires_at=now + self._session_ttl_seconds,
                staged_path=staged_path,
                suggestions={},
            )
            self._sessions[import_id] = session
            # The native selection is no longer authoritative after this point.
            # Every later operation uses the one private immutable snapshot above.
            self.selection_provider.revoke(selection_id)
            return PersonalImportPreview(self._status(session), preview)

    def status(self, import_id: str) -> PersonalImportStatus:
        with self._lock:
            return self._status(self._require_session(import_id))

    def suggest(
        self,
        import_id: str,
        *,
        sheet_index: int,
    ) -> PersonalImportSuggestion:
        """Return one bounded AI candidate; this never confirms or writes data."""

        context = self.prepare_suggestion_context(import_id, sheet_index=sheet_index)
        with self._lock:
            session = self._require_session(import_id)
            if session.suggestions is not None and sheet_index in session.suggestions:
                return session.suggestions[sheet_index]
            if self._suggestion_model is None:
                raise _service_error(
                    "personal_ai_not_configured",
                    "尚未配置 DeepSeek；仍可使用本地预览并手动检查。",
                    retryable=False,
                )
            model = self._suggestion_model
        return self.suggest_from_prepared_context(
            context,
            model=model,
            provider=self._suggestion_provider_label,
        )

    def prepare_suggestion_context(
        self,
        import_id: str,
        *,
        sheet_index: int,
    ) -> PersonalSuggestionContext:
        """Freeze the exact bounded prompt input without calling a model."""

        with self._lock:
            return self._suggestion_context_locked(import_id, sheet_index)

    def has_cached_suggestion(self, import_id: str, *, sheet_index: int) -> bool:
        """Tell a server-side assembler whether a paid suggestion already exists."""

        with self._lock:
            session = self._require_session(import_id)
            self._suggestion_context_locked(import_id, sheet_index)
            return bool(
                session.suggestions is not None
                and sheet_index in session.suggestions
            )

    def suggestion_context_fingerprint(
        self,
        import_id: str,
        *,
        sheet_index: int,
    ) -> str:
        """Return only the current prompt-context fingerprint for stale checks."""

        return self.prepare_suggestion_context(
            import_id,
            sheet_index=sheet_index,
        ).preview_fingerprint

    def suggest_from_prepared_context(
        self,
        context: PersonalSuggestionContext,
        *,
        model: PersonalSuggestionModel,
        provider: str,
        allow_cached: bool = True,
    ) -> PersonalImportSuggestion:
        """Run and validate one exact prepared call; never save or confirm data."""

        if not isinstance(context, PersonalSuggestionContext):
            raise _service_error(
                "personal_request_invalid",
                "AI 识别请求无效。",
                details={"field": "context"},
            )
        key = (context.import_id, context.sheet_index)
        with self._lock:
            current_context = self._suggestion_context_locked(*key)
            if (
                current_context.preview_fingerprint != context.preview_fingerprint
                or current_context.outbound_fingerprint != context.outbound_fingerprint
                or current_context.messages != context.messages
                or current_context.prompt_warnings != context.prompt_warnings
                or current_context.byte_count != context.byte_count
            ):
                raise _service_error(
                    "personal_ai_context_changed",
                    "表格预览已变化，请重新生成识别请求。",
                    retryable=True,
                )
            session = self._require_session(context.import_id)
            if (
                session.suggestions is not None
                and context.sheet_index in session.suggestions
            ):
                if allow_cached:
                    return session.suggestions[context.sheet_index]
                raise _service_error(
                    "personal_ai_already_suggested",
                    "该表格已有识别建议，请直接查看现有结果。",
                    retryable=False,
                )
            if key in self._suggestions_in_flight:
                raise _service_error(
                    "personal_ai_busy",
                    "该表格正在生成识别建议，请稍候。",
                    retryable=True,
                )
            sheet = session.preview.sheets[context.sheet_index]
            self._suggestions_in_flight.add(key)

        try:
            payload = model.request_json(
                [dict(message) for message in context.messages],
                task="analysis",
                max_tokens=6_000,
                thinking=False,
                temperature=0.1,
            )
        except Exception as exc:
            with self._lock:
                self._suggestions_in_flight.discard(key)
            if type(exc).__name__ == "DeepSeekNotConfigured":
                raise _service_error(
                    "personal_ai_not_configured",
                    "尚未配置 AI；仍可使用本地预览并手动检查。",
                    retryable=False,
                ) from exc
            raise _service_error(
                "personal_ai_unavailable",
                "AI 暂时无法完成识别；本地预览不受影响。",
                retryable=True,
            ) from exc
        try:
            suggestion = validate_suggestion_payload(
                import_id=context.import_id,
                sheet_index=context.sheet_index,
                sheet=sheet,
                payload=payload,
                inherited_warnings=context.prompt_warnings,
                provider=" ".join(str(provider or "AI").split())[:80] or "AI",
            )
        except PersonalImportSuggestionError as exc:
            with self._lock:
                self._suggestions_in_flight.discard(key)
            raise _service_error(
                "personal_ai_invalid_response",
                "AI 返回的识别建议未通过本地校验，请重试或直接检查本地预览。",
                retryable=True,
            ) from exc
        with self._lock:
            self._suggestions_in_flight.discard(key)
            latest = self._suggestion_context_locked(*key)
            if (
                latest.preview_fingerprint != context.preview_fingerprint
                or latest.outbound_fingerprint != context.outbound_fingerprint
            ):
                raise _service_error(
                    "personal_ai_context_changed",
                    "表格预览已变化，请重新生成识别请求。",
                    retryable=True,
                )
            current = self._require_session(context.import_id)
            if current.suggestions is None:
                current.suggestions = {}
            current.suggestions[context.sheet_index] = suggestion
            return suggestion

    def _suggestion_context_locked(
        self,
        import_id: str,
        sheet_index: int,
    ) -> PersonalSuggestionContext:
        session = self._require_session(import_id)
        if (
            isinstance(sheet_index, bool)
            or not isinstance(sheet_index, int)
            or not 0 <= sheet_index < len(session.preview.sheets)
        ):
            raise _service_error(
                "personal_request_invalid",
                "工作表选择无效。",
                details={"field": "sheet_index"},
            )
        sheet = session.preview.sheets[sheet_index]
        messages, warnings = build_suggestion_messages(sheet)
        outbound_canonical = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        preview_canonical = json.dumps(
            {
                "source_sha256": sheet.source_file.sha256,
                "sheet_name": sheet.sheet_name,
                "row_count": int(sheet.row_count),
                "columns": [column.as_dict() for column in sheet.columns],
                "sample_rows": [dict(row) for row in sheet.sample_rows],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return PersonalSuggestionContext(
            import_id=import_id,
            sheet_index=sheet_index,
            messages=tuple(MappingProxyType(dict(message)) for message in messages),
            prompt_warnings=tuple(warnings),
            preview_fingerprint=hashlib.sha256(preview_canonical).hexdigest(),
            outbound_fingerprint=hashlib.sha256(outbound_canonical).hexdigest(),
            byte_count=len(outbound_canonical),
            sheet_name=sheet.sheet_name,
            row_count=sheet.row_count,
            column_count=len(sheet.columns),
            sample_row_count=min(len(sheet.sample_rows), 5),
        )

    def import_reviewed(
        self,
        import_id: str,
        payload: Mapping[str, Any],
        *,
        reviewed: bool,
    ) -> PersonalImportStatus:
        """Persist then confirm one user-reviewed form as a single product action.

        The model can populate the form, but only the literal ``reviewed=True``
        supplied by the user's final action marks the currently visible fields as
        reviewed.  The repository still observes the existing draft -> confirm CAS.
        """

        if reviewed is not True:
            raise _service_error(
                "personal_review_required",
                "请先检查当前识别结果，再确认导入。",
                details={"field": "reviewed"},
            )
        reviewed_payload = self._mark_payload_reviewed(payload)
        fingerprint = hashlib.sha256(
            json.dumps(
                reviewed_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            session = self._require_session(import_id)
            if session.stage is PersonalImportStage.INDEXABLE:
                if session.reviewed_payload_fingerprint == fingerprint:
                    return self._status(session)
                raise _service_error(
                    "personal_import_already_confirmed",
                    "该实验数据已经确认，不能被其他内容覆盖。",
                    details={"stage": session.stage.value},
                )

            if session.stage is PersonalImportStage.DRAFT_SAVED and session.draft is not None:
                candidate, project, sample, requested_revision = self._build_draft(
                    session,
                    reviewed_payload,
                )
                if (
                    requested_revision is not None
                    and requested_revision != session.revision
                ):
                    raise _service_error(
                        "RUN_REVISION_CONFLICT",
                        "实验草稿已被更新，请重新加载后再确认。",
                        details={
                            "expected_revision": requested_revision,
                            "actual_revision": session.revision,
                        },
                    )
                if (
                    candidate == session.draft
                    and project == session.project
                    and sample == session.sample
                    and session.revision is not None
                ):
                    confirmed = self.confirm(import_id, expected_revision=session.revision)
                    session.reviewed_payload_fingerprint = fingerprint
                    return confirmed
                reviewed_payload["expected_revision"] = session.revision

            draft_status = self.save_draft(import_id, reviewed_payload)
            if draft_status.revision is None:
                raise _service_error(
                    "personal_draft_save_failed",
                    "实验草稿未能安全保存。",
                    retryable=True,
                )
            confirmed = self.confirm(
                import_id,
                expected_revision=draft_status.revision,
            )
            session.reviewed_payload_fingerprint = fingerprint
            return confirmed

    def save_draft(
        self,
        import_id: str,
        payload: Mapping[str, Any],
    ) -> PersonalImportStatus:
        with self._lock:
            session = self._require_session(import_id)
            if session.stage is PersonalImportStage.INDEXABLE:
                raise _service_error(
                    "personal_import_already_confirmed",
                    "该实验数据已经确认，不能被旧草稿覆盖。",
                    details={"stage": session.stage.value},
                )
            draft, project, sample, expected_revision = self._build_draft(
                session,
                payload,
            )
            if session.stage is PersonalImportStage.PREVIEWED:
                if expected_revision is not None:
                    raise _service_error(
                        "personal_revision_invalid",
                        "首次保存草稿不应携带旧版本。",
                        details={"field": "expected_revision"},
                    )
                if session.pending_draft is None:
                    session.pending_draft = draft
                    session.project = project
                    session.sample = sample
                elif (
                    session.pending_draft != draft
                    or session.project != project
                    or session.sample != sample
                ):
                    raise _service_error(
                        "personal_draft_retry_mismatch",
                        "上次保存未完成，请使用原内容重试或重新预览。",
                        details={"stage": session.stage.value},
                    )
            else:
                if expected_revision != session.revision:
                    raise _service_error(
                        "RUN_REVISION_CONFLICT",
                        "实验草稿已被更新，请重新加载后再保存。",
                        details={
                            "expected_revision": expected_revision,
                            "actual_revision": session.revision,
                        },
                    )
                if session.project != project or session.sample != sample:
                    raise _service_error(
                        "RUN_PARENT_IMMUTABLE",
                        "现有草稿不能移动到其他项目或样品。",
                        details={"entity_type": "experiment_run"},
                    )

            repository = self._repository()
            try:
                if not session.project_created:
                    repository.add_project(project)
                    session.project_created = True
                if not session.sample_created:
                    repository.add_sample(sample)
                    session.sample_created = True
                if not session.file_registered:
                    if session.staged_path is None:
                        raise _service_error(
                            "personal_snapshot_unavailable",
                            "导入快照已失效，请重新选择文件。",
                            retryable=True,
                        )
                    repository.register_source_file(
                        draft.preview.source_file,
                        session.staged_path,
                    )
                    session.file_registered = True
                    self._remove_staged_path(session.staged_path)
                    session.staged_path = None
                result = repository.save_experiment(
                    draft,
                    project_id=session.project_id,
                    sample_id=session.sample_id,
                    expected_revision=(
                        expected_revision
                        if session.stage is PersonalImportStage.DRAFT_SAVED
                        else None
                    ),
                )
            except PrivateRepositoryError as exc:
                raise _translate_repository(exc) from exc

            self._accept_draft_result(session, draft, result)
            session.project = project
            session.sample = sample
            session.pending_draft = draft
            return self._status(session)

    def confirm(
        self,
        import_id: str,
        *,
        expected_revision: int,
    ) -> PersonalImportStatus:
        with self._lock:
            session = self._require_session(import_id)
            if session.stage is PersonalImportStage.INDEXABLE:
                raise _service_error(
                    "personal_import_already_confirmed",
                    "该实验数据已经确认，不能重复确认。",
                    details={"stage": session.stage.value},
                )
            if session.stage is not PersonalImportStage.DRAFT_SAVED or session.draft is None:
                raise _service_error(
                    "INVALID_IMPORT_TRANSITION",
                    "请先保存草稿，再确认实验数据。",
                    details={
                        "current_state": session.stage.value,
                        "required_state": PersonalImportStage.DRAFT_SAVED.value,
                    },
                )
            if (
                isinstance(expected_revision, bool)
                or not isinstance(expected_revision, int)
                or expected_revision != session.revision
            ):
                raise _service_error(
                    "RUN_REVISION_CONFLICT",
                    "实验草稿已被更新，请重新加载后再确认。",
                    details={
                        "expected_revision": expected_revision,
                        "actual_revision": session.revision,
                    },
                )
            if not session.draft.ready_to_confirm:
                issue_codes = list(
                    dict.fromkeys(
                        issue.partition(":")[0]
                        for issue in session.draft.confirmation_issues()
                    )
                )
                raise _service_error(
                    "RUN_CONFIRMATION_INCOMPLETE",
                    "请明确确认每列的角色、含义和单位后再继续。",
                    details={
                        "entity_type": "experiment_run",
                        "issue_codes": issue_codes,
                        "required_state": "confirmed",
                    },
                )
            confirmed = replace(session.draft, confirmation_state="confirmed")
            try:
                result = self._repository().save_experiment(
                    confirmed,
                    project_id=session.project_id,
                    sample_id=session.sample_id,
                    expected_revision=expected_revision,
                )
            except PrivateRepositoryError as exc:
                raise _translate_repository(exc) from exc
            if not result.indexable or result.confirmation_state != "confirmed":
                raise _service_error(
                    "personal_confirmation_failed",
                    "实验数据未能安全进入私人检索。",
                    retryable=True,
                )
            session.draft = confirmed
            session.pending_draft = confirmed
            session.stage = PersonalImportStage.INDEXABLE
            session.revision = result.revision
            return self._status(session)

    def private_search_source(self) -> PrivateRepositorySearchSource:
        """Injection point for the frozen federated search composition."""

        return PrivateRepositorySearchSource(self._repository())

    def private_search_snapshot(self) -> PrivateSearchSnapshot:
        """Return one immutable source view for atomic federated-search refresh."""

        return self.private_search_source().snapshot()

    def _validated_new_import_id(self) -> str:
        import_id = self._import_id_factory()
        if (
            not isinstance(import_id, str)
            or _IMPORT_ID_RE.fullmatch(import_id) is None
            or import_id in self._sessions
        ):
            raise _service_error(
                "personal_import_identity_invalid",
                "个人实验导入身份无效。",
            )
        return import_id

    def _stage_snapshot(
        self,
        source_path: Path,
        *,
        preview: TabularFilePreview,
        import_id: str,
    ) -> Path:
        """Persist the provider snapshot once, then verify it against the preview."""

        staging_root = self.data_root / ".import-staging"
        try:
            self.data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_status = self.data_root.lstat()
            if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
                raise OSError("unsafe personal data root")
            staging_root.mkdir(mode=0o700, exist_ok=True)
            staging_status = staging_root.lstat()
            if stat.S_ISLNK(staging_status.st_mode) or not stat.S_ISDIR(staging_status.st_mode):
                raise OSError("unsafe personal staging root")
            os.chmod(staging_root, 0o700)
        except OSError as exc:
            raise _service_error(
                "personal_snapshot_failed",
                "无法建立私有导入快照，请稍后重试。",
                retryable=True,
            ) from exc

        suffix = Path(preview.source_file.original_name).suffix.casefold()
        destination = staging_root / f"{import_id}-{secrets.token_urlsafe(8)}{suffix}"
        source_fd = -1
        output_fd = -1
        copied = 0
        digest = hashlib.sha256()
        try:
            source_fd = os.open(
                source_path,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            source_status = os.fstat(source_fd)
            if not stat.S_ISREG(source_status.st_mode):
                raise OSError("snapshot source is not a regular file")
            output_fd = os.open(
                destination,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            while chunk := os.read(source_fd, 1024 * 1024):
                copied += len(chunk)
                if copied > self._preview_limits.max_file_bytes:
                    raise OSError("snapshot exceeds bounded preview limit")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output_fd, view)
                    if written <= 0:
                        raise OSError("snapshot write did not progress")
                    view = view[written:]
            os.fsync(output_fd)
            if (
                copied != preview.source_file.size_bytes
                or copied != source_status.st_size
                or digest.hexdigest() != preview.source_file.sha256
            ):
                raise _service_error(
                    "personal_selection_changed",
                    "文件在预览过程中发生变化，请重新选择。",
                    retryable=True,
                )
            return destination
        except PersonalImportServiceError:
            self._remove_staged_path(destination)
            raise
        except OSError as exc:
            self._remove_staged_path(destination)
            raise _service_error(
                "personal_snapshot_failed",
                "无法保存私有导入快照，请重新选择文件。",
                retryable=True,
            ) from exc
        finally:
            if output_fd >= 0:
                os.close(output_fd)
            if source_fd >= 0:
                os.close(source_fd)

    @staticmethod
    def _remove_staged_path(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The repository and public state never depend on cleanup succeeding.
            # A future session prune can retry without exposing the private path.
            pass

    def _cleanup_orphaned_staging(self) -> None:
        """Remove only expired, service-owned snapshots left by an earlier process."""

        staging_root = self.data_root / ".import-staging"
        try:
            root_status = self.data_root.lstat()
            staging_status = staging_root.lstat()
            if (
                stat.S_ISLNK(root_status.st_mode)
                or not stat.S_ISDIR(root_status.st_mode)
                or stat.S_ISLNK(staging_status.st_mode)
                or not stat.S_ISDIR(staging_status.st_mode)
            ):
                return
            cutoff = time.time() - self._session_ttl_seconds
            for candidate in staging_root.iterdir():
                if _STAGED_FILENAME_RE.fullmatch(candidate.name) is None:
                    continue
                candidate_status = candidate.lstat()
                if (
                    stat.S_ISREG(candidate_status.st_mode)
                    and candidate_status.st_mtime <= cutoff
                ):
                    self._remove_staged_path(candidate)
        except OSError:
            # Cleanup is best effort and never weakens repository availability.
            return

    @staticmethod
    def _mark_payload_reviewed(raw_payload: Mapping[str, Any]) -> dict[str, Any]:
        payload = _require_object(raw_payload, "request")
        columns = payload.get("columns")
        if not isinstance(columns, list):
            raise _service_error(
                "personal_request_invalid",
                "列映射必须完整对应预览列。",
                details={"field": "columns"},
            )
        reviewed_columns: list[dict[str, Any]] = []
        for position, raw in enumerate(columns):
            column = _require_object(raw, f"columns[{position}]")
            column["role_confirmed"] = True
            column["meaning_confirmed"] = True
            column["unit_confirmed"] = True
            reviewed_columns.append(column)
        payload["columns"] = reviewed_columns
        return payload

    def _repository(self) -> PrivateExperimentRepository:
        if self._repository_value is not None:
            return self._repository_value
        try:
            repository = self._repository_factory(self.data_root)
        except PrivateRepositoryError as exc:
            raise _translate_repository(exc) from exc
        self._repository_value = repository
        return repository

    def _require_session(self, import_id: str) -> _ImportSession:
        if not isinstance(import_id, str) or _IMPORT_ID_RE.fullmatch(import_id) is None:
            raise _service_error(
                "personal_import_session_invalid",
                "个人实验导入状态无效。",
            )
        now = self._clock()
        self._prune(now)
        session = self._sessions.get(import_id)
        if session is None:
            raise _service_error(
                "personal_import_session_expired",
                "个人实验导入状态已过期，请重新选择文件。",
                retryable=True,
            )
        return session

    def _prune(self, now: float) -> None:
        for import_id in tuple(self._sessions):
            session = self._sessions[import_id]
            if session.expires_at <= now:
                self._sessions.pop(import_id, None)
                self._suggestions_in_flight = {
                    key for key in self._suggestions_in_flight if key[0] != import_id
                }
                if session.staged_path is not None:
                    self._remove_staged_path(session.staged_path)
                self.selection_provider.revoke(session.selection_id)

    @staticmethod
    def _status(session: _ImportSession) -> PersonalImportStatus:
        return PersonalImportStatus(
            import_id=session.import_id,
            stage=session.stage,
            source_file=session.preview.source_file.as_public_dict(),
            confirmation_state=(
                "confirmed"
                if session.stage is PersonalImportStage.INDEXABLE
                else "draft"
                if session.stage is PersonalImportStage.DRAFT_SAVED
                else "unconfirmed"
            ),
            indexable=session.stage is PersonalImportStage.INDEXABLE,
            revision=session.revision,
        )

    @staticmethod
    def _accept_draft_result(
        session: _ImportSession,
        draft: PersonalExperimentDraft,
        result: PrivateOperationResult,
    ) -> None:
        if (
            result.import_state != "draft_saved"
            or result.confirmation_state != "draft"
            or result.indexable
            or result.revision is None
        ):
            raise _service_error(
                "personal_draft_save_failed",
                "实验草稿未能安全保存。",
                retryable=True,
            )
        session.draft = draft
        session.stage = PersonalImportStage.DRAFT_SAVED
        session.revision = result.revision

    def _build_draft(
        self,
        session: _ImportSession,
        raw_payload: Mapping[str, Any],
    ) -> tuple[PersonalExperimentDraft, PrivateProject, PrivateSample, int | None]:
        payload = _require_object(raw_payload, "request")
        _require_exact_keys(
            payload,
            required={"sheet_index", "project", "sample", "run", "columns", "series"},
            optional={"expected_revision"},
            field="request",
        )
        sheet_index = payload["sheet_index"]
        if (
            isinstance(sheet_index, bool)
            or not isinstance(sheet_index, int)
            or not 0 <= sheet_index < len(session.preview.sheets)
        ):
            raise _service_error(
                "personal_request_invalid",
                "工作表选择无效。",
                details={"field": "sheet_index"},
            )
        source_sheet = session.preview.sheets[sheet_index]
        project_data = _require_object(payload["project"], "project")
        sample_data = _require_object(payload["sample"], "sample")
        run_data = _require_object(payload["run"], "run")
        _require_exact_keys(
            project_data,
            required={"name"},
            optional={"description"},
            field="project",
        )
        _require_exact_keys(
            sample_data,
            required={"name"},
            optional={"material", "description"},
            field="sample",
        )
        _require_exact_keys(
            run_data,
            required={"name", "method", "conditions"},
            optional={"user_note"},
            field="run",
        )
        project = PrivateProject(
            project_id=session.project_id,
            name=_required_text(project_data["name"], "project.name"),
            description=_optional_text(project_data.get("description"), "project.description"),
        )
        sample = PrivateSample(
            sample_id=session.sample_id,
            project_id=session.project_id,
            name=_required_text(sample_data["name"], "sample.name"),
            material=_optional_text(sample_data.get("material"), "sample.material", limit=500),
            description=_optional_text(sample_data.get("description"), "sample.description"),
        )
        columns = self._columns(source_sheet, payload["columns"])
        series = self._series(payload["series"])
        conditions_raw = _require_object(run_data["conditions"], "run.conditions")
        if len(conditions_raw) > 64:
            raise _service_error(
                "personal_request_invalid",
                "实验条件数量超过安全限制。",
                details={"field": "run.conditions"},
            )
        conditions = {
            _required_text(key, "condition.name", limit=200): _required_text(
                value,
                "condition.value",
            )
            for key, value in conditions_raw.items()
        }
        preview = TabularImportPreview(
            source_file=source_sheet.source_file,
            sheet_name=source_sheet.sheet_name,
            row_count=source_sheet.row_count,
            columns=columns,
        )
        try:
            draft = PersonalExperimentDraft(
                draft_id=session.draft_id,
                project_name=project.name,
                run_name=_required_text(run_data["name"], "run.name"),
                sample_name=sample.name,
                method=_required_text(run_data["method"], "run.method"),
                preview=preview,
                series=series,
                conditions=conditions,
                user_note=_optional_text(run_data.get("user_note"), "run.user_note"),
                confirmation_state="draft",
            )
        except ValueError as exc:
            raise _service_error(
                "personal_request_invalid",
                "个人实验草稿内容无效。",
            ) from exc
        expected_revision = payload.get("expected_revision")
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise _service_error(
                "personal_revision_invalid",
                "实验记录版本无效，请重新加载。",
                details={"field": "expected_revision"},
            )
        return draft, project, sample, expected_revision

    @staticmethod
    def _columns(
        source_sheet: TabularImportPreview,
        raw_columns: Any,
    ) -> tuple[ColumnMapping, ...]:
        if not isinstance(raw_columns, list) or len(raw_columns) != len(source_sheet.columns):
            raise _service_error(
                "personal_request_invalid",
                "列映射必须完整对应预览列。",
                details={"field": "columns"},
            )
        source_by_name = {column.source_name: column for column in source_sheet.columns}
        output: dict[str, ColumnMapping] = {}
        for position, raw in enumerate(raw_columns):
            value = _require_object(raw, f"columns[{position}]")
            _require_exact_keys(
                value,
                required={
                    "source_name",
                    "role",
                    "role_confirmed",
                    "meaning",
                    "meaning_confirmed",
                    "unit",
                    "unit_confirmed",
                },
                field="columns",
            )
            source_name = _required_text(value["source_name"], "column.source_name")
            source = source_by_name.get(source_name)
            if source is None or source_name in output:
                raise _service_error(
                    "personal_request_invalid",
                    "列映射必须完整对应预览列。",
                    details={"field": "columns"},
                )
            try:
                output[source_name] = ColumnMapping(
                    source_name=source_name,
                    role=_required_text(value["role"], "column.role", limit=40),
                    data_type=source.data_type,
                    role_confirmed=_required_bool(
                        value["role_confirmed"], "column.role_confirmed"
                    ),
                    meaning=_optional_text(value["meaning"], "column.meaning", limit=500),
                    meaning_confirmed=_required_bool(
                        value["meaning_confirmed"], "column.meaning_confirmed"
                    ),
                    unit=_optional_text(value["unit"], "column.unit", limit=80),
                    unit_confirmed=_required_bool(
                        value["unit_confirmed"], "column.unit_confirmed"
                    ),
                    sample_values=source.sample_values,
                )
            except ValueError as exc:
                raise _service_error(
                    "personal_request_invalid",
                    "列映射内容无效。",
                    details={"field": "columns"},
                ) from exc
        if set(output) != set(source_by_name):
            raise _service_error(
                "personal_request_invalid",
                "列映射必须完整对应预览列。",
                details={"field": "columns"},
            )
        return tuple(output[column.source_name] for column in source_sheet.columns)

    @staticmethod
    def _series(raw_series: Any) -> tuple[MeasurementSeriesDraft, ...]:
        if not isinstance(raw_series, list) or len(raw_series) > 128:
            raise _service_error(
                "personal_request_invalid",
                "测量序列格式无效。",
                details={"field": "series"},
            )
        output: list[MeasurementSeriesDraft] = []
        for position, raw in enumerate(raw_series):
            value = _require_object(raw, f"series[{position}]")
            _require_exact_keys(
                value,
                required={"series_id", "name", "x_column", "y_column"},
                optional={"uncertainty_column", "description"},
                field="series",
            )
            try:
                output.append(
                    MeasurementSeriesDraft(
                        series_id=_required_text(
                            value["series_id"], "series.series_id", limit=240
                        ),
                        name=_required_text(value["name"], "series.name"),
                        x_column=_required_text(value["x_column"], "series.x_column"),
                        y_column=_required_text(value["y_column"], "series.y_column"),
                        uncertainty_column=_optional_text(
                            value.get("uncertainty_column"),
                            "series.uncertainty_column",
                            limit=500,
                        ),
                        description=_optional_text(
                            value.get("description"), "series.description"
                        ),
                    )
                )
            except ValueError as exc:
                raise _service_error(
                    "personal_request_invalid",
                    "测量序列内容无效。",
                    details={"field": "series"},
                ) from exc
        return tuple(output)
