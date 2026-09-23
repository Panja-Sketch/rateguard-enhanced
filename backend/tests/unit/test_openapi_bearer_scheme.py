"""Locked-brief requirement (Prompt 8 completion, section 3.D): the generated
OpenAPI schema must declare an HTTP Bearer security scheme, and authenticated
routes must carry a `security` requirement referencing it, so `/docs` renders
a functional "Authorize" control. Authentication itself is still done by
`app.auth.dependencies._extract_bearer_token`/`get_current_user` -- this only
covers that FastAPI's OpenAPI generation actually reflects that reality
instead of silently omitting it because auth was wired via raw `Request`
header access rather than FastAPI's `Security(...)` machinery.
"""

from app.main import app


def test_openapi_declares_http_bearer_security_scheme():
    schema = app.openapi()
    security_schemes = schema.get("components", {}).get("securitySchemes", {})

    assert "HTTPBearer" in security_schemes
    assert security_schemes["HTTPBearer"]["type"] == "http"
    assert security_schemes["HTTPBearer"]["scheme"] == "bearer"


def test_an_authenticated_route_carries_the_bearer_security_requirement():
    schema = app.openapi()
    paths = schema["paths"]
    missions_post = paths["/api/v1/missions"]["post"]

    assert missions_post.get("security") == [{"HTTPBearer": []}]
