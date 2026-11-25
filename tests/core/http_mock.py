"""Lightweight async HTTP mock replacing aioresponses for aiohttp 3.14 compatibility."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, cast

import aiohttp


@dataclass
class FakeHTTPResponse:
    """Minimal mock aiohttp response."""

    status: int = 200
    payload: Any = None
    body_text: str = ""

    async def json(self) -> Any:
        """Return parsed JSON payload."""
        if self.payload is not None:
            return self.payload
        return json.loads(self.body_text)

    async def text(self) -> str:
        """Return response body as text."""
        if self.body_text:
            return self.body_text
        return json.dumps(self.payload or {})


class _ResponseContext:
    def __init__(self, result: FakeHTTPResponse | Exception) -> None:
        self._result = result

    async def __aenter__(self) -> FakeHTTPResponse:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeClientSession:
    """Drop-in test stub implementing aiohttp.ClientSession.get/post."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], deque[tuple[FakeHTTPResponse | Exception, bool]]] = {}
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def add_get(
        self,
        url: str,
        *,
        status: int = 200,
        payload: Any = None,
        body_text: str = "",
        exc: Exception | None = None,
        repeat: bool = False,
    ) -> None:
        """Register a mocked GET response for an exact base URL."""
        res: FakeHTTPResponse | Exception = exc or FakeHTTPResponse(
            status=status, payload=payload, body_text=body_text
        )
        self._routes.setdefault(("GET", url), deque()).append((res, repeat))

    def add_post(
        self,
        url: str,
        *,
        status: int = 200,
        payload: Any = None,
        body_text: str = "",
        exc: Exception | None = None,
        repeat: bool = False,
    ) -> None:
        """Register a mocked POST response for an exact base URL."""
        res: FakeHTTPResponse | Exception = exc or FakeHTTPResponse(
            status=status, payload=payload, body_text=body_text
        )
        self._routes.setdefault(("POST", url), deque()).append((res, repeat))

    def get(self, url: str, **kwargs: Any) -> _ResponseContext:
        """Handle mocked GET request."""
        return self._dispatch("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> _ResponseContext:
        """Handle mocked POST request."""
        return self._dispatch("POST", url, kwargs)

    def _dispatch(
        self, method: str, url: str, kwargs: dict[str, Any]
    ) -> _ResponseContext:
        self.requests.append((method, url, kwargs))
        queue = self._routes.get((method, url))
        if not queue:
            return _ResponseContext(FakeHTTPResponse(status=404, payload={}))
        result, repeat = queue[0]
        if not repeat:
            queue.popleft()
        return _ResponseContext(result)

    def as_aiohttp_session(self) -> aiohttp.ClientSession:
        """Cast self to aiohttp.ClientSession for strict type checkers."""
        return cast(aiohttp.ClientSession, self)
