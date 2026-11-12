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

## Supported versions

Only the latest `main` is supported. Fixes land on `main` and are released as a
new tag when appropriate.
