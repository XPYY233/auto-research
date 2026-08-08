from __future__ import annotations

import re
import secrets
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

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
        self._sessions: dict[str, _ImportSession] = {}
        self._lock = threading.RLock()

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
            try:
                with self.selection_provider.snapshot(selection_id) as selected:
                    preview = self._previewer(
                        selected.path,
                        limits=self._preview_limits,
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

            import_id = self._import_id_factory()
            if not isinstance(import_id, str) or _IMPORT_ID_RE.fullmatch(import_id) is None:
                raise _service_error(
                    "personal_import_identity_invalid",
                    "个人实验导入身份无效。",
                )
            if import_id in self._sessions:
                raise _service_error(
                    "personal_import_identity_invalid",
                    "个人实验导入身份无效。",
                )
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
            )
            self._sessions[import_id] = session
            return PersonalImportPreview(self._status(session), preview)

    def status(self, import_id: str) -> PersonalImportStatus:
        with self._lock:
            return self._status(self._require_session(import_id))

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
                    with self.selection_provider.snapshot(session.selection_id) as selected:
                        repository.register_source_file(
                            draft.preview.source_file,
                            selected.path,
                        )
                    session.file_registered = True
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
            except SelectionSnapshotProviderError as exc:
                raise _translate_selection(exc) from exc
            except PrivateRepositoryError as exc:
                raise _translate_repository(exc) from exc

            self._accept_draft_result(session, draft, result)
            session.project = project
            session.sample = sample
            session.pending_draft = draft
            self.selection_provider.revoke(session.selection_id)
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
            if self._sessions[import_id].expires_at <= now:
                self._sessions.pop(import_id, None)

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
