class IpirV2Error(Exception):
    """Base error for IPIR v0.2 contract violations."""


class LoweringNotSupportedError(IpirV2Error):
    """Raised when `compat.lower_to_v0_1` encounters a v0.2 construct it does
    not (yet) have a faithful v0.1 runtime equivalent for. Always raised
    explicitly rather than silently producing an approximate or wrong lowered
    package — see docs/implementation/DECISIONS.md (D2)."""


class CompatibilityGateError(IpirV2Error):
    """Raised for a structural compatibility-gate input error (not a mismatch
    finding itself, which is returned as data via CompatibilityResult)."""
