"""Client for the optional single-writer sidecar.

Only the HTTP boundary lives here; the row shape stays in ``RunStore`` so the
local and remote paths cannot drift.
"""

from __future__ import annotations

import httpx

from shotplan.domain.models import PlanResult
from shotplan.storage import RunStore, StorageError


class RemoteRunSink:
    """Ship one finished run to ``shotplan.writer_service`` and wait for commit."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.url,
            timeout=timeout,
            trust_env=False,
            transport=transport,
            # The sidecar is loopback and cheap to reconnect to; reusing a
            # stale socket here would look like a 30s server stall.
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    def save(self, result: PlanResult) -> str:
        row = RunStore.row_for(result)
        try:
            response = self._client.post("/save", json={"rows": [list(row)]})
        except httpx.HTTPError as exc:
            raise StorageError(f"single-writer unreachable: {exc}") from exc
        if response.status_code != 200:
            raise StorageError(
                f"single-writer rejected run {result.trace.run_id}: "
                f"{response.status_code} {response.text[:200]}"
            )
        return result.trace.run_id

    def close(self) -> None:
        self._client.close()
