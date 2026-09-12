class AdapterError(Exception):
    """Base exception for source pricing adapter errors."""

    pass


class SourceParsingError(AdapterError):
    """Raised when source format parsing fails."""

    pass


class MappingCompletenessError(AdapterError):
    """Raised when critical required pricing fields cannot be mapped."""

    pass
