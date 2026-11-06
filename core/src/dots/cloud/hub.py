"""Calls the hub's HTTP API: the Macs in the pool, the credit ledger, the model it serves, and answers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp


class HubError(Exception):
    """The hub didn't answer, or answered with an error. The message says which."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class HubClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def _get(self, path: str) -> dict:
        try:
            async with self.session.get(self.base_url + path, timeout=aiohttp.ClientTimeout(total=8)) as r:
                body = await r.json(content_type=None)
                if r.status >= 400:
                    raise HubError(r.status, _message(body, r.status))
                return body
        except aiohttp.ClientError as e:
            raise HubError(503, f"the hub at {self.base_url} didn't answer: {e}") from e

    async def nodes(self) -> list[dict]:
        return (await self._get("/v1/pool/nodes")).get("nodes", [])

    async def ledger(self) -> dict:
        return await self._get("/v1/credits")

    async def models(self) -> dict[str, float]:
        """The models the hub serves, with each one's recent speed in tokens a second (0 before any turn)."""
        return {m["id"]: float(m.get("tok_per_sec") or 0) for m in (await self._get("/v1/models")).get("data", [])}

    async def complete(self, body: dict, account: str) -> dict:
        """A whole answer, in OpenAI's chat completions format, charged to `account`."""
        try:
            async with self.session.post(self.base_url + "/v1/chat/completions", json={**body, "stream": False},
                                         headers={"X-Dots-Account": account},
                                         timeout=aiohttp.ClientTimeout(total=300)) as r:
                reply = await r.json(content_type=None)
                if r.status >= 400:
                    raise HubError(r.status, _message(reply, r.status))
                return reply
        except aiohttp.ClientError as e:
            raise HubError(503, f"the hub didn't answer: {e}") from e

    async def stream(self, body: dict, account: str, speculate: bool = True) -> AsyncIterator[bytes]:
        """An answer as it is written, charged to `account`: the hub's event lines, passed through as they come.
        `speculate` False asks the pool not to use a draft model for it."""
        headers = {"X-Dots-Account": account}
        if not speculate:
            headers["X-Dots-Speculate"] = "off"
        try:
            async with self.session.post(self.base_url + "/v1/chat/completions", json={**body, "stream": True},
                                         headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=300)) as r:
                if r.status >= 400:
                    raise HubError(r.status, _message(await r.json(content_type=None), r.status))
                async for line in r.content:
                    yield line
        except aiohttp.ClientError as e:
            raise HubError(503, f"the hub didn't answer: {e}") from e


def _message(body: object, status: int) -> str:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message", status))
    return f"hub returned {status}"
