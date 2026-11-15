"""Fast-forward a GitHub branch that is many commits behind, via the REST API.

`push_via_api.py` handles exactly one commit: it rebuilds local HEAD with the
remote HEAD as parent. When the remote is 34 commits behind, that check fails
(and `--force` would create a divergent squash commit, not the real history).

This script walks `remote..HEAD` in order and rebuilds EACH commit with its
own real parent, tree and metadata, so every remote SHA equals the local one.
It refuses to do anything unless the remote ref is an ancestor of local HEAD,
i.e. a genuine fast-forward with no remote-only commits to overwrite.

    python scripts/push_range_via_api.py --dry-run
    python scripts/push_range_via_api.py
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from typing import Any

from push_via_api import GitHub, credential_token, git, parse_remote


def sh(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def commit_meta(commit: str) -> tuple[str, dict[str, str], dict[str, str]]:
    raw = git("cat-file", "commit", commit, text=False)
    assert isinstance(raw, bytes)
    _, _, msg = raw.partition(b"\n\n")
    message = msg.decode("utf-8")
    an, ae, ai, cn, ce, ci = git(
        "log", "-1", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI", commit
    ).rstrip("\n").split("\0")
    return (
        message,
        {"name": an, "email": ae, "date": ai},
        {"name": cn, "email": ce, "date": ci},
    )


def upload_changes(
    gh: GitHub, repo: str, parent: str, commit: str, dry_run: bool
) -> tuple[list[dict[str, Any]], int]:
    modes: dict[str, str] = {}
    for line in git("ls-tree", "-r", commit).splitlines():
        mode, rest = line.split(" ", 1)
        _, path = rest.split("\t", 1)
        modes[path] = mode

    entries: list[dict[str, Any]] = []
    uploads = 0
    for line in git("diff", "--name-status", parent, commit).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        if status == "D":
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        if status == "R":
            entries.append({"path": parts[1], "mode": "100644", "type": "blob", "sha": None})
        blob = git("cat-file", "blob", f"{commit}:{path}", text=False)
        assert isinstance(blob, bytes)
        local_sha = git("rev-parse", f"{commit}:{path}").strip()
        if not dry_run:
            result = gh("POST", f"/repos/{repo}/git/blobs", {
                "content": base64.b64encode(blob).decode(), "encoding": "base64",
            })
            if result["sha"] != local_sha:
                raise RuntimeError(f"blob SHA mismatch for {path} in {commit[:8]}")
        uploads += 1
        entries.append({
            "path": path, "mode": modes.get(path, "100644"),
            "type": "blob", "sha": local_sha,
        })
    return entries, uploads


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--branch", default=None)
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = args.repo or parse_remote(sh("remote", "get-url", args.remote))
    branch = args.branch or sh("rev-parse", "--abbrev-ref", "HEAD")
    head = sh("rev-parse", "HEAD")

    gh = GitHub(credential_token("github.com"))
    ref = gh("GET", f"/repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    print(f"repo={repo} branch={branch}\nlocal  HEAD: {head}\nremote HEAD: {ref}")

    if ref == head:
        print("already up to date")
        return 0

    # Safety: a true fast-forward, nothing remote-only can be lost.
    check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ref, head],
        capture_output=True,
    )
    if check.returncode != 0:
        raise RuntimeError(
            "remote HEAD is NOT an ancestor of local HEAD; refusing to rewrite "
            "remote history. Fetch/rebase first and inspect the divergence."
        )
    ahead = sh("rev-list", "--count", f"{ref}..HEAD")
    behind = sh("rev-list", "--count", f"HEAD..{ref}")
    if behind != "0":
        raise RuntimeError(f"remote has {behind} commit(s) not in local history; abort")
    commits = sh("rev-list", "--reverse", f"{ref}..HEAD").splitlines()
    print(f"fast-forward: {ahead} commits, 0 remote-only commits")

    total_changes = 0
    for index, commit in enumerate(commits, 1):
        parent = sh("rev-parse", f"{commit}^")
        tree = sh("rev-parse", f"{commit}^{{tree}}")
        base_tree = sh("rev-parse", f"{parent}^{{tree}}")
        entries, uploads = upload_changes(gh, repo, parent, commit, args.dry_run)
        total_changes += len(entries)
        subject = sh("log", "-1", "--format=%s", commit)

        if args.dry_run:
            print(f"  [{index}/{len(commits)}] {commit[:8]} {len(entries)} files  {subject[:60]}")
            continue

        new_tree = gh("POST", f"/repos/{repo}/git/trees",
                      {"base_tree": base_tree, "tree": entries})["sha"]
        if new_tree != tree:
            raise RuntimeError(f"tree mismatch at {commit[:8]}: {new_tree} != {tree}")
        message, author, committer = commit_meta(commit)
        created = gh("POST", f"/repos/{repo}/git/commits", {
            "message": message, "tree": new_tree, "parents": [parent],
            "author": author, "committer": committer,
        })["sha"]
        if created != commit:
            raise RuntimeError(f"SHA mismatch at {commit[:8]}: {created} != {commit}")
        print(f"  [{index}/{len(commits)}] {commit[:8]} OK  {subject[:60]}", flush=True)

    if args.dry_run:
        print(f"dry-run: {len(commits)} commits, {total_changes} file changes; nothing written")
        return 0

    gh("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": head, "force": False})
    subprocess.check_call(
        ["git", "update-ref", f"refs/remotes/{args.remote}/{branch}", head]
    )
    print(f"pushed {branch} -> {head} ({len(commits)} commits, {total_changes} file changes)")
    print(f"repo: https://github.com/{repo}")
    print(f"CI:   https://github.com/{repo}/actions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
