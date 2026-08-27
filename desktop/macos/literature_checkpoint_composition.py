from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from auto_research.evidence.literature_checkpoint_runtime import (
    LiteratureCheckpointRuntime,
)
from auto_research.evidence.literature_snapshot_blob import (
    SealedImmutablePDFBlobStore,
)
from auto_research.evidence.literature_task_checkpoint import (
    CheckpointSealer,
)
from auto_research.evidence.literature_task_checkpoint_service import (
    LiteratureTaskCheckpointService,
)
from auto_research.evidence.literature_task_checkpoint_store import (
    SealedSQLiteLiteratureCheckpointStore,
)

from literature_checkpoint_security import (
    AuthenticatedCheckpointSealer,
    default_authenticated_checkpoint_sealer,
)
from secure_history import HistoryKeyProvider


DEFAULT_LITERATURE_PRIVATE_ROOT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Auto Research"
    / "Private Data"
    / "Literature Tasks"
)
_KEYCHAIN_ENVIRONMENT = "AUTO_RESEARCH_MACOS_CREDENTIAL_STORE"


@dataclass(frozen=True)
class MacLiteratureCheckpointServices:
    sealer: CheckpointSealer
    checkpoint_store: SealedSQLiteLiteratureCheckpointStore
    checkpoint_service: LiteratureTaskCheckpointService
    checkpoint_runtime: LiteratureCheckpointRuntime
    snapshot_blobs: SealedImmutablePDFBlobStore


def create_mac_literature_checkpoint_services(
    *,
    private_root: Path | str = DEFAULT_LITERATURE_PRIVATE_ROOT,
    stable_signed: bool | None = None,
    key_provider: HistoryKeyProvider | None = None,
    clock: Callable[[], int] | None = None,
) -> MacLiteratureCheckpointServices:
    """Compose the platform security adapters without scientific logic."""

    root = Path(private_root)
    _prepare_private_root(root)
    use_keychain = (
        os.environ.get(_KEYCHAIN_ENVIRONMENT, "").strip().casefold() == "keychain"
        if stable_signed is None
        else stable_signed
    )
    if not isinstance(use_keychain, bool):
        raise ValueError("stable_signed must be a boolean")
    sealer: AuthenticatedCheckpointSealer = default_authenticated_checkpoint_sealer(
        stable_signed=use_keychain,
        key_provider=key_provider,
        private_directory=root,
    )
    checkpoint_store = SealedSQLiteLiteratureCheckpointStore(
        data_root=root / "checkpoints-v1",
        sealer=sealer,
    )
    checkpoint_service = LiteratureTaskCheckpointService(
        store=checkpoint_store,
        clock=clock,
    )
    return MacLiteratureCheckpointServices(
        sealer=sealer,
        checkpoint_store=checkpoint_store,
        checkpoint_service=checkpoint_service,
        checkpoint_runtime=LiteratureCheckpointRuntime(checkpoint_service),
        snapshot_blobs=SealedImmutablePDFBlobStore(
            data_root=root / "pdf-snapshots-v1",
            sealer=sealer,
        ),
    )


def _prepare_private_root(root: Path) -> None:
    try:
        if root.exists() or root.is_symlink():
            metadata = root.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise OSError
        else:
            root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
    except OSError:
        raise RuntimeError("literature checkpoint private storage is unavailable") from None


__all__ = [
    "DEFAULT_LITERATURE_PRIVATE_ROOT",
    "MacLiteratureCheckpointServices",
    "create_mac_literature_checkpoint_services",
]
