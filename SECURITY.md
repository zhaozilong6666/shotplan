# Security policy

## Scope

shotplan is a portfolio / research project. It runs with no credentials by default
(`SHOTPLAN_PROVIDER=mock`) and stores run history in a local SQLite file. The
following are in scope:

- Prompt-injection or data-exfiltration paths in the model provider layer
- Crashes or unbounded resource use caused by a malformed brief or knowledge file
- Secret leakage through logs, traces, API responses or the committed repository
- Path traversal or unsafe file access in the CLI / API entry points

Out of scope: multi-tenant auth, payment, real booking integrations — none of
these exist in this project (see README "Known limitations").

## Reporting

Please **do not open a public issue** for a vulnerability. Use GitHub's private
vulnerability reporting: **Security → Advisories → Report a vulnerability** on
the repository page. Include steps to reproduce, affected version/commit, and
the impact you believe it has.

You can expect an initial response within a few days. This is a personal
project, so there is no paid bounty.

## Secrets

- `.env` is git-ignored and must never be committed.
- `python -m shotplan.cli config` prints configuration with the API key masked.
- Public demos must run with `SHOTPLAN_PROVIDER=mock` and no real key.
- The API push helper (`scripts/push_via_api.py`) reads the token from
  `git credential fill`, never prints it and never writes it to disk.

## Network exposure

- CORS is not enabled and there is no wildcard fallback: the bundled UI is
  served same-origin. A separate frontend must be added to an explicit
  allow-list, never `allow_origins=["*"]`.
- Every response carries `X-Request-ID`. Client-supplied ids are validated
  against `[A-Za-z0-9._-]{1,64}`, so they cannot forge log lines or inject
  response headers.
- Access logs contain method, path, status and duration only — never request
  bodies or the API key.
- When `SHOTPLAN_API_KEY` is set, `/api/plan`, `/api/runs*`, `/api/summary`
  and `/api/stats` require it. `/api/health`, `/api/config` and `/api/rules`
  stay public — they only expose version, masked configuration and the rule
  book, never run history. The bundled web UI does not send the header, so a
  key-protected deployment should serve it behind a proxy that injects the key
  or use a separate frontend.
- The rate limiter is in-process. With multiple uvicorn workers each worker
  enforces its own counter, so a public multi-worker deployment must enforce
  the real limit at the gateway instead of trusting this one. Behind a reverse
  proxy, `request.client.host` is the proxy unless trusted forwarded headers
  are enabled; do not trust `X-Forwarded-For` blindly.

## Supported versions

Only the latest `main` is supported. Fixes land on `main` and are released as a
new tag when appropriate.
