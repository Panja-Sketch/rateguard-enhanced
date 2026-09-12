from app.adapters.base import PricingSourceAdapter
from app.adapters.errors import AdapterError
from app.adapters.models import SourceFormat


class AdapterRegistry:
    """Central registry mapping SourceFormat to PricingSourceAdapter implementations."""

    def __init__(self) -> None:
        self._adapters: dict[SourceFormat, PricingSourceAdapter] = {}

    def register(self, source_format: SourceFormat, adapter: PricingSourceAdapter) -> None:
        self._adapters[source_format] = adapter

    def get_adapter(self, source_format: SourceFormat) -> PricingSourceAdapter:
        adapter = self._adapters.get(source_format)
        if not adapter:
            raise AdapterError(f"No adapter registered for source format '{source_format.value}'")
        return adapter


# Global Singleton Registry
_registry = AdapterRegistry()


def get_adapter_registry() -> AdapterRegistry:
    return _registry
