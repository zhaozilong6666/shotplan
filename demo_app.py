"""Launcher for the public demo.

The published demo must never call a paid model: it runs on someone else's
credit, it would take 70-100s per request on the relay, and the container has
no business holding an API key. So this script pins the provider to `mock`
*before* the app reads any configuration.

`os.environ` wins over `.env` because the loader uses `setdefault`, which means
this holds even if a local `.env` is uploaded by accident. That is deliberate:
the safe choice is the default, and the override is one line of code someone
has to delete on purpose.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["SHOTPLAN_PROVIDER"] = "mock"
os.environ.setdefault("SHOTPLAN_DB_PATH", str(Path(tempfile.gettempdir()) / "shotplan-demo.db"))
os.environ.setdefault("SHOTPLAN_PERSIST_MODE", "sync")
os.environ.setdefault("SHOTPLAN_RATE_LIMIT_PER_MINUTE", "30")
os.environ.setdefault("SHOTPLAN_LOG_LEVEL", "INFO")

import uvicorn  # noqa: E402  (import after env is pinned)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("shotplan.api:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
