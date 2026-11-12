"""Push the current HEAD to GitHub through the REST Git Data API.

Use this when ``git push`` cannot reach github.com (for example a corporate
proxy that only allows api.github.com and returns 502 on CONNECT). The remote
commit is rebuilt with the same tree, message and author/committer metadata as
the local commit, so its SHA is identical and the two repositories never
diverge. Requires only the Python standard library.

Security: the token comes from ``git credential fill`` (i.e. the credential
helper you already logged in with). It stays in memory, is never printed and is
never written to a file. Use ``--dry-run`` to inspect the plan without writing.

Usage:
    python scripts/push_via_api.py                 # push current branch to origin
    python scripts/push_via_api.py --dry-run
    python scripts/push_via_api.py --repo owner/name --branch main
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"


def git(*args: str, text: bool = True) -> str | bytes:
    out = subprocess.check_output(["git", *args], text=text)
    return out


def credential_token(host: str) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol=https\nhost={host}\n\n",
        capture_output=True, text=True, timeout=30, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError("no GitHub credential available; log in first")
    creds = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    token = creds.get("password", "")
    if not token:
        raise RuntimeError("credential helper returned no token")
    return token


class GitHub:
    def __init__(self, token: str) -> None:
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "shotplan-push-via-api",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def __call__(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(API + path, data=data, headers=self.headers, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()[:300]}"
            ) from exc


def parse_remote(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("git@") and ":" in url:
        return url.split(":", 1)[1].removesuffix(".git")
    marker = "github.com/"
    if marker in url:
        return url.split(marker, 1)[1].removesuffix(".git")
    raise RuntimeError(f"cannot parse GitHub repo from remote url: {url!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None, help="owner/name (default: parse origin)")
    ap.add_argument("--branch", default=None, help="branch (default: current branch)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--force", action="store_true", help="allow non-fast-forward update")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = args.repo or parse_remote(git("remote", "get-url", args.remote))
    branch = args.branch or git("rev-parse", "--abbrev-ref", "HEAD").strip()
    head = git("rev-parse", "HEAD").strip()
    tree = git("rev-parse", "HEAD^{tree}").strip()
    parents = git("rev-parse", "HEAD^").strip().splitlines() if _has_parent() else []
    parent = parents[0] if parents else ""

    token = credential_token("github.com")
    gh = GitHub(token)
    ref = gh("GET", f"/repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    print(f"repo={repo} branch={branch}")
    print(f"local  HEAD: {head}")
    print(f"remote HEAD: {ref}")
    if ref == head:
        print("already up to date")
        return 0
    if not args.force and parent and ref != parent:
        raise RuntimeError(
            "remote is not the parent of local HEAD; fetch/rebase first "
            "(or use --force if you know what you are doing)"
        )

    raw = git("cat-file", "commit", "HEAD", text=False)
    assert isinstance(raw, bytes)
    _, _, msg = raw.partition(b"\n\n")
    message = msg.decode("utf-8")
    meta = git("log", "-1", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI")
    an, ae, ai, cn, ce, ci = meta.rstrip("\n").split("\0")

    remote_tree = gh("GET", f"/repos/{repo}/git/commits/{ref}")["tree"]["sha"]
    modes = {}
    for line in git("ls-tree", "-r", "HEAD").splitlines():
        mode, rest = line.split(" ", 1)
        _, path = rest.split("\t", 1)
        modes[path] = mode

    changes = [ln for ln in git("diff", "--name-status", ref, "HEAD").splitlines() if ln.strip()]
    entries: list[dict[str, Any]] = []
    uploads = 0
    for line in changes:
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        if status == "D":
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        if status == "R":  # rename: remove the old path, upload the new one
            entries.append({"path": parts[1], "mode": "100644", "type": "blob", "sha": None})
        blob = git("cat-file", "blob", f"HEAD:{path}", text=False)
        assert isinstance(blob, bytes)
        local_sha = git("rev-parse", f"HEAD:{path}").strip()
        if not args.dry_run:
            result = gh("POST", f"/repos/{repo}/git/blobs", {
                "content": base64.b64encode(blob).decode(), "encoding": "base64",
            })
            if result["sha"] != local_sha:
                raise RuntimeError(f"blob SHA mismatch for {path}: line endings or mode differ")
        uploads += 1
        entries.append({
            "path": path, "mode": modes.get(path, "100644"),
            "type": "blob", "sha": local_sha,
        })
    print(f"changes: {len(changes)} files ({uploads} blobs, {len(changes) - uploads} deletions)")

    if args.dry_run:
        print("dry-run: no remote objects created, no ref updated")
        return 0

    new_tree = gh("POST", f"/repos/{repo}/git/trees",
                  {"base_tree": remote_tree, "tree": entries})["sha"]
    if new_tree != tree:
        raise RuntimeError(f"tree SHA mismatch: {new_tree} != {tree}")
    print(f"tree OK: {new_tree}")

    created = gh("POST", f"/repos/{repo}/git/commits", {
        "message": message, "tree": new_tree,
        "parents": [ref] if ref else [],
        "author": {"name": an, "email": ae, "date": ai},
        "committer": {"name": cn, "email": ce, "date": ci},
    })["sha"]
    if created != head:
        raise RuntimeError(f"commit SHA mismatch: {created} != {head}")
    print(f"commit OK: {created}")

    gh("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": created, "force": args.force})
    subprocess.check_call(
        ["git", "update-ref", f"refs/remotes/{args.remote}/{branch}", created]
    )
    print(f"pushed {branch} -> {created}")
    print(f"CI: https://github.com/{repo}/actions")
    return 0


def _has_parent() -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
