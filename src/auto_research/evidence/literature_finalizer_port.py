from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class TrustedAtomicLiteratureFinalizer(ABC):
    """Nominal trust boundary for one audited scientific transaction.

    Implementations are selected by trusted composition.  A nominal base class
    deliberately rejects arbitrary duck-typed ``finalize`` objects while
    keeping the extraction job independent of the concrete database writer.
    """

    @abstractmethod
    def finalize(self, package: Any) -> Mapping[str, Any]:
        """Atomically publish one already validated literature package."""


__all__ = ["TrustedAtomicLiteratureFinalizer"]
