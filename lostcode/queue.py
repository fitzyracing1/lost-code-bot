"""findings/queue.json: one entry per finding, deduped across runs by a stable id."""
from __future__ import annotations

import hashlib
import json
import os
import datetime as dt

STATES = ("new", "verified", "fixed-on-fork", "approved", "sent", "rejected")


def finding_id(repo, kind, file, ref):
    return hashlib.sha1(f"{repo}|{kind}|{file}|{ref}".encode()).hexdigest()[:12]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path):
    if not os.path.exists(path):
        return {"version": 1, "items": {}}
    with open(path) as fh:
        return json.load(fh)


def save(q, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    q["items"] = dict(sorted(q["items"].items()))
    with open(path, "w") as fh:
        json.dump(q, fh, indent=2, sort_keys=False)
        fh.write("\n")


def merge(q, findings):
    """Add new findings, refresh last_seen on known ones. Never touches a human-set state."""
    added = 0
    for f in findings:
        fid = finding_id(f["repo"], f["kind"], f["file"], f["ref"])
        item = q["items"].get(fid)
        if item:
            item["last_seen"] = now()
            for k in ("head_sha", "line", "confidence", "evidence", "recovery_sha", "deleted_in", "live_alternative"):
                if f.get(k) is not None:
                    item.setdefault("finding", {})[k] = f[k]
            continue
        q["items"][fid] = {"id": fid, "state": "new", "first_seen": now(), "last_seen": now(),
                           "history": [{"at": now(), "state": "new", "by": "scanner"}], "finding": f}
        added += 1
    return added


def set_state(q, fid, state, by, note=""):
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    item = q["items"][fid]
    if state == "approved" and by == "bot":
        raise PermissionError("only a human may set approved")
    item["state"] = state
    item["history"].append({"at": now(), "state": state, "by": by, "note": note})
    return item
