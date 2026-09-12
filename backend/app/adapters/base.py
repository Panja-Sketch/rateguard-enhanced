from abc import ABC, abstractmethod
from typing import Any

from app.adapters.models import AdapterResult, SourceDescriptor


class PricingSourceAdapter(ABC):
    """Generic abstract base class for all RateGuard source-agnostic pricing adapters."""

    adapter_id: str
    supported_format: str

    @abstractmethod
    def to_ipir(
        self,
        source: SourceDescriptor,
        content: bytes,
        context: dict[str, Any] | None = None,
    ) -> AdapterResult:
        """Compiles raw source bytes into a canonical IPIRPackage with mapping metadata."""
        pass
