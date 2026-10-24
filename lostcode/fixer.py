"""Prepare fixes on throwaway branches of fitzyracing1's FORK only.

This module never talks to the upstream repo's PRs, issues or comments.
Upstream sending lives in send.py and needs a human-set `approved` state.
"""
from __future__ import annotations

import os
import posixpath
import subprocess
import tempfile

FORK_OWNER = os.environ.get("LOSTCODE_FORK_OWNER", "fitzyracing1")
MIN_CONFIDENCE = 0.85


def ref_base(f):
    """Directory the reference was resolved against."""
    ref = posixpath.normpath(f["ref"].split("#")[0].lstrip("/")) if not f["ref"].startswith("/") else ""
    p = f["path"]
    if f["ref"].startswith("/"):
        return ""
    if f["kind"] in ("doc", "script", "import") and posixpath.normpath(posixpath.join(posixpath.dirname(f["file"]), f["ref"].split("#")[0])) == p:
        return posixpath.dirname(f["file"])
    if ref and p.endswith(ref):
        return p[: -len(ref)].rstrip("/")
    return posixpath.dirname(f["file"])


def plan(item):
    f = item["finding"]
    if f.get("live_alternative"):
        base = ref_base(f)
        new = posixpath.relpath(f["live_alternative"], base or ".")
        if f["ref"].startswith("./") and not new.startswith("."):
            new = "./" + new
        if f["ref"].startswith("/"):
            new = "/" + f["live_alternative"]
        anchor = "#" + f["ref"].split("#", 1)[1] if "#" in f["ref"] else ""
        return {"action": "rewrite-reference", "file": f["file"], "old": f["ref"], "new": new + anchor,
                "summary": f"Point `{f['ref']}` in `{f['file']}` at the file's current location `{f['live_alternative']}`."}
    if f.get("recovery_sha"):
        return {"action": "restore-file", "path": f["path"], "from_sha": f["recovery_sha"],
                "summary": f"Restore `{f['path']}` from {f['recovery_sha'][:10]} (last commit where it existed)."}
    return None


def pr_body(item, p):
    f = item["finding"]
    lines = [f"### Lost reference: `{f['ref']}`", "",
             f"`{f['file']}` (line {f['line']}) refers to `{f['path']}`, which does not exist on the default branch.", ""]
    if f.get("deleted_in"):
        lines.append(f"The file was removed in {f['deleted_in']}; it last existed at {f.get('recovery_sha')}.")
    lines += ["", "**Evidence**", *[f"- {e}" for e in f.get("evidence", [])], "",
              "**Change**", p["summary"] if p else "No automatic fix; needs a maintainer decision.", "",
              "Found by an automated scan and checked by a human before sending. Happy to adjust or close."]
    return "\n".join(lines)


def branch_name(item):
    return f"try/lost-code-{item['id']}"


def eligible(item):
    return item["state"] == "verified" and item["finding"].get("confidence", 0) >= MIN_CONFIDENCE


def prepare(item, apply=False, workdir=None, runner=subprocess.run):
    """Dry-run by default: returns the plan and PR body without touching GitHub."""
    p = plan(item)
    body = pr_body(item, p)
    result = {"id": item["id"], "repo": item["finding"]["repo"], "branch": branch_name(item),
              "fork": f"{FORK_OWNER}/{item['finding']['repo'].split('/')[1]}", "plan": p, "pr_body": body,
              "applied": False}
    if not apply or p is None:
        return result
    repo = item["finding"]["repo"]
    fork = result["fork"]
    workdir = workdir or tempfile.mkdtemp(prefix="lostcode-fix-")
    sh = lambda *a, **k: runner(list(a), check=True, capture_output=True, text=True, **k)
    # fork only; --clone=false so nothing else happens upstream
    sh("gh", "repo", "fork", repo, "--clone=false", "--default-branch-only")
    sh("git", "clone", "--quiet", "--depth", "50", f"https://github.com/{fork}.git", workdir)
    sh("git", "-C", workdir, "checkout", "-b", result["branch"])
    if p["action"] == "rewrite-reference":
        fp = os.path.join(workdir, p["file"])
        with open(fp) as fh:
            text = fh.read()
        with open(fp, "w") as fh:
            fh.write(text.replace(p["old"], p["new"]))
    else:
        sh("git", "-C", workdir, "fetch", "--quiet", "--depth", "1", f"https://github.com/{repo}.git", p["from_sha"])
        sh("git", "-C", workdir, "checkout", p["from_sha"], "--", p["path"])
    sh("git", "-C", workdir, "commit", "-am", f"Fix lost reference {item['finding']['ref']}")
    sh("git", "-C", workdir, "push", "--quiet", "origin", result["branch"])  # origin == the fork
    result["applied"] = True
    return result
