from __future__ import annotations

from typing import Protocol, Sequence

from package_input import PACKAGE_EXTENSION, PackageInputBroker, PackageInputHandle


class PackageWindowBridge(Protocol):
    def choose_files(self, *, title: str, extensions: tuple[str, ...], multiple: bool) -> Sequence[str]: ...


class PackageInputWindowAdapter:
    """Route picker, drop, and file-association inputs through one broker."""

    def __init__(self, broker: PackageInputBroker, window: PackageWindowBridge) -> None:
        self.broker = broker
        self.window = window

    def choose_package(self) -> PackageInputHandle:
        candidates = self.window.choose_files(
            title="选择 Auto Research 资料包",
            extensions=(PACKAGE_EXTENSION,),
            multiple=False,
        )
        return self.broker.accept(candidates, source="file-picker")

    def accept_drop(self, candidates: Sequence[str]) -> PackageInputHandle:
        return self.broker.accept(candidates, source="drag-drop")

    def accept_file_association(self, arguments: Sequence[str]) -> PackageInputHandle:
        return self.broker.accept(arguments, source="file-association")
