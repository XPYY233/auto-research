from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_research.personal.import_service import (
    DEFAULT_IMPORT_SESSION_TTL_SECONDS,
    DEFAULT_MAX_IMPORT_SESSIONS,
    PersonalImportPreview,
    PersonalImportService as SharedPersonalImportService,
    PersonalImportServiceError,
    PersonalImportStage,
    PersonalImportStatus,
    SelectionSnapshot,
    SelectionSnapshotProvider,
    SelectionSnapshotProviderError,
)
from personal_file_selection_broker import PersonalFileSelectionBroker


DEFAULT_PERSONAL_LIBRARY_DIRECTORY = "private-library"


class PersonalImportService(SharedPersonalImportService):
    """Compatibility adapter for macOS callers created before the shared core."""

    def __init__(
        self,
        *,
        data_root: Path | str,
        broker: PersonalFileSelectionBroker | None = None,
        selection_provider: SelectionSnapshotProvider | None = None,
        **kwargs: Any,
    ) -> None:
        if broker is not None and selection_provider is not None:
            raise ValueError("provide either broker or selection_provider, not both")
        provider = selection_provider or broker or PersonalFileSelectionBroker()
        super().__init__(
            data_root=data_root,
            selection_provider=provider,
            **kwargs,
        )

    @property
    def broker(self) -> SelectionSnapshotProvider:
        return self.selection_provider


__all__ = [
    "DEFAULT_IMPORT_SESSION_TTL_SECONDS",
    "DEFAULT_MAX_IMPORT_SESSIONS",
    "DEFAULT_PERSONAL_LIBRARY_DIRECTORY",
    "PersonalImportPreview",
    "PersonalImportService",
    "PersonalImportServiceError",
    "PersonalImportStage",
    "PersonalImportStatus",
    "SelectionSnapshot",
    "SelectionSnapshotProvider",
    "SelectionSnapshotProviderError",
]
