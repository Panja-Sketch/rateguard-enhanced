import re

# Locked doc section 6.2: "IDs match ^[a-z][a-z0-9_]{1,63}$ and are unique
# within the package namespace." Stricter than v0.1's ID_PATTERN
# (app.ipir.common.ID_PATTERN), so v0.2 enforces its own pattern rather than
# loosening the v0.1 one for every existing package.
ID_PATTERN_V2 = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def validate_identifier_string_v2(v: str) -> str:
    """Validates a v0.2 identifier against the locked lowercase/underscore
    pattern. Distinct from `app.ipir.common.validate_identifier_string`
    (v0.1's more permissive pattern) so v0.1 packages already in production
    are not retroactively invalidated."""
    v_str = str(v).strip()
    if not ID_PATTERN_V2.match(v_str):
        raise ValueError(
            f"IPIR v0.2 identifier '{v_str}' must match ^[a-z][a-z0-9_]{{1,63}}$ "
            "(lowercase letters, digits, underscores; must start with a letter)."
        )
    return v_str
