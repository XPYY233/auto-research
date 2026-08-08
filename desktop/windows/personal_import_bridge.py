from __future__ import annotations

from typing import Any, Callable, Mapping

from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
)


PrivateSourceListener = Callable[[object, str], None]


class PersonalImportBridgeAdapter:
    """Path-free projection over the shared personal import state machine."""

    def __init__(
        self,
        service: PersonalImportService,
        *,
        private_source_listener: PrivateSourceListener | None = None,
    ) -> None:
        self.service = service
        self.private_source_listener = private_source_listener

    def preview(self, selection_id: str) -> dict[str, Any]:
        return self.service.preview(selection_id).public_dict()

    def status(self, import_id: str) -> dict[str, Any]:
        return self.service.status(import_id).public_dict()

    def save_draft(self, import_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.service.save_draft(import_id, payload).public_dict()

    def confirm(self, import_id: str, *, expected_revision: int) -> dict[str, Any]:
        status = self.service.confirm(
            import_id,
            expected_revision=expected_revision,
        )
        if self.private_source_listener is not None:
            self.private_source_listener(
                self.service.private_search_source(),
                f"revision:{status.revision}",
            )
        return status.public_dict()

    @staticmethod
    def public_error(error: BaseException) -> dict[str, Any]:
        if isinstance(error, PersonalImportServiceError):
            return error.public_dict()
        return {
            "schema_version": "personal-import-error-v1",
            "code": "personal_import_failed",
            "message": "个人实验导入暂时无法完成。",
            "retryable": True,
        }
