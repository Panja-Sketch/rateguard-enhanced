"""Auth failures carry a stable machine code and a fixed, safe message.

Neither the message nor the code ever contains token contents, claim values,
or library exception text."""

from fastapi import HTTPException, status


def unauthorized(code: str = "AUTHENTICATION_REQUIRED") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": "Authentication is required or the credential is invalid."},
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(code: str = "FORBIDDEN") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": "You do not have permission to perform this action."},
    )


def auth_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "AUTH_SERVICE_UNAVAILABLE", "message": "Authentication service is temporarily unavailable."},
    )
