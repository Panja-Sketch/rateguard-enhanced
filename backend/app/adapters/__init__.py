from app.adapters.base import PricingSourceAdapter
from app.adapters.errors import AdapterError, MappingCompletenessError, SourceParsingError
from app.adapters.excel import ExcelPricingAdapter
from app.adapters.models import AdapterResult, SourceDescriptor, SourceFormat
from app.adapters.pdf import PDFPricingAdapter
from app.adapters.platform_config import PlatformConfigAdapter
from app.adapters.registry import AdapterRegistry, get_adapter_registry
from app.adapters.structured_json import StructuredJSONPricingAdapter

# Automatically register default adapter implementations
_reg = get_adapter_registry()
_reg.register(SourceFormat.STRUCTURED_JSON, StructuredJSONPricingAdapter())
_reg.register(SourceFormat.EXCEL, ExcelPricingAdapter())
_reg.register(SourceFormat.PDF, PDFPricingAdapter())
_reg.register(SourceFormat.PLATFORM_CONFIG, PlatformConfigAdapter())


__all__ = [
    "AdapterError",
    "AdapterRegistry",
    "AdapterResult",
    "ExcelPricingAdapter",
    "MappingCompletenessError",
    "PDFPricingAdapter",
    "PlatformConfigAdapter",
    "PricingSourceAdapter",
    "SourceDescriptor",
    "SourceFormat",
    "SourceParsingError",
    "StructuredJSONPricingAdapter",
    "get_adapter_registry",
]
