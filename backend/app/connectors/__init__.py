"""The versioned REST rating-engine connector (locked doc section 8, CP8).

This package is the *client* side that a future mission pipeline will call
to reach an administrator-allowlisted target rating engine (today, exactly
one real entry: `backend/rating_engine`, the demo service built in session
1). Nothing in this package accepts an arbitrary URL from a mission or a
normal user — callers select a `connector_id` and `engine_version` from the
fixed, config-driven registry in `app.connectors.registry`.

Nothing in the existing mission pipeline (`app/agents/supervisor.py`,
`app/api/*`, `app/missions/*`) imports from this package yet — wiring it
into a live mission stage is explicitly future "integration session" work
(mirrors D3's own session-1 scoping). See docs/implementation/DECISIONS.md
D7 for this session's implementation-shape decisions.
"""
