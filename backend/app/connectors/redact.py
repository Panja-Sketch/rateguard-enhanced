"""Log-redaction for the REST rating-engine connector.

Mirrors `app.agents.gemini_client`'s `_SECRET_PATTERNS`/`_scrub_secrets`
pattern exactly (same regexes, same replacement text) rather than
inventing a divergent redaction scheme, per the CP8 task instructions.

Kept as its own small module instead of importing directly from
`app.agents.gemini_client` to avoid creating a dependency from
`app.connectors` (a networking/SSRF-sensitive module) onto the Gemini
client module for a two-line helper. If a third consumer ever needs this,
it should be lifted to a shared `app.core` helper at that point rather than
copied a third time.
"""

import re

_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z_-]{10,}"),
)


def scrub_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
