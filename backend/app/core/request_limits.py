"""Reject oversized request bodies before they are buffered (locked doc 15.3).

Pure-ASGI so it can count streamed (chunked) bodies as well as trusting a
declared Content-Length. The response is a fixed, safe 413.

For a chunked body the limit is enforced by aborting the read; FastAPI wraps a
failed body read into its own 400, so once the limit has been hit this
middleware replaces whatever response the application produced with the 413."""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(Exception):
    pass


def _too_large_body(code: str = "REQUEST_TOO_LARGE") -> bytes:
    return json.dumps({"detail": {"code": code, "message": "The request body is too large or malformed."}}).encode()


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send, status=400, code="BAD_CONTENT_LENGTH")
                return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                # Swallow whatever the app tried to answer with; 413 goes out below.
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if exceeded and not response_started:
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send, status: int = 413, code: str = "REQUEST_TOO_LARGE") -> None:
        body = _too_large_body(code)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
