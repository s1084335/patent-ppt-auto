"""MCP http 傳輸的 bearer token 中介層（第一版：內網 token，安全從簡定案）。

純 ASGI middleware，包在 FastMCP 的 streamable-http app 外層：
Authorization 標頭必須為 'Bearer <PATENT_MCP_TOKEN>'，否則一律回 401，不進 MCP 處理。
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


class BearerTokenMiddleware:
    """檢查 Authorization: Bearer <token>；不符回 401。"""

    def __init__(self, app: Any, token: str):
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: dict, receive: Callable[[], Awaitable], send: Callable) -> None:
        if scope.get("type") == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"").decode("latin-1")
            if provided != self._expected:
                await self._reject(send)
                return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(send: Callable) -> None:
        body = json.dumps({"error": "unauthorized", "detail": "invalid or missing bearer token"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
