"""The ONLY place that may open an upstream PR. Manual, one finding at a time,
and only when Joshua has set the finding to `approved` himself."""
from __future__ import annotations

import subprocess

from . import fixer


class NotApproved(Exception):
    pass


def check(item):
    if item["state"] != "approved":
        raise NotApproved(f"{item['id']} is '{item['state']}', not 'approved'")
    last = item["history"][-1]
    if last.get("state") != "approved" or last.get("by") in ("scanner", "bot", "fixer", "ci"):
        raise NotApproved("approval must be set by a human (lostcode approve ID --by joshua)")
    if not item.get("fork_branch"):
        raise NotApproved("no fork branch recorded; run the fixer with --apply first")


def send(item, runner=subprocess.run):
    check(item)
    f = item["finding"]
    head = f"{fixer.FORK_OWNER}:{item['fork_branch']}"
    out = runner(["gh", "pr", "create", "-R", f["repo"], "--head", head,
                  "--title", f"Fix lost reference to {f['path']}", "--body", item.get("pr_body", "")],
                 check=True, capture_output=True, text=True)
    return out.stdout.strip()
