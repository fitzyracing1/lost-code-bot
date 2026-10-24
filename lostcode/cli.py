"""lostcode: find, queue and (carefully) fix lost code in public GitHub repos."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

from . import detect, fixer, queue, send as sender
from .gh import GitHub, RateLimited, clone, log

QUEUE = "findings/queue.json"


def lookup_history(gh, f, branch, budget):
    """Find the commit that removed the path and the parent SHA where it last existed."""
    if budget[0] <= 0:
        return
    budget[0] -= 1
    c = gh.last_commit_touching(f.repo, f.path, branch)
    if c:
        f.deleted_in = c["sha"]
        f.recovery_sha = c["parent"]
        f.evidence.append(f"last touched by {c['sha'][:10]} ({c['date']}): {c['message']}; "
                          f"recover from parent {str(c['parent'])[:10]}")
    else:
        f.evidence.append("no commit on the default branch ever touched this path")


def scan_repo(repo, gh, per_repo_lookups=25, max_findings=40):
    tmp = tempfile.mkdtemp(prefix="lostcode-")
    try:
        head, branch, files = clone(repo, os.path.join(tmp, "r"))
        root = os.path.join(tmp, "r")

        def reader(p):
            fp = os.path.join(root, p)
            if not os.path.isfile(fp):
                return ""
            with open(fp, errors="ignore") as fh:
                return fh.read()

        found = detect.scan_tree(detect.Tree(files, reader), repo)
        # noisy repos (doc sites) get capped; prefer high-signal kinds first
        order = {"ci": 0, "script": 1, "import": 2, "doc": 3}
        found.sort(key=lambda f: order.get(f.kind, 9))
        found = found[:max_findings]
        budget = [per_repo_lookups]
        for f in found:
            f.head_sha = head
            if f.kind != "doc" or len(found) <= 15:
                try:
                    lookup_history(gh, f, branch, budget)
                except RateLimited as e:
                    log(f"  rate limit: {e}")
                    budget[0] = 0
            f.confidence = detect.score(f)
        return found
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_scan(a):
    gh = GitHub(min_remaining=a.min_remaining)
    repos = a.repo or []
    if a.top:
        repos += gh.top_repos(a.top)
    results, errors, all_f = {}, {}, []
    t0 = time.time()
    for r in repos:
        log(f"scanning {r}")
        try:
            fs = scan_repo(r, gh, a.lookups, a.max_per_repo)
            results[r] = len(fs)
            all_f += [f.to_dict() for f in fs if f.confidence >= a.min_confidence]
        except Exception as e:  # keep going, record it
            errors[r] = f"{type(e).__name__}: {str(e)[:200]}"
            log(f"  error: {errors[r]}")
    out = {"scanned_at": queue.now(), "repos": results, "errors": errors, "api_calls": gh.calls,
           "rate_remaining": gh.remaining, "seconds": round(time.time() - t0), "findings": all_f}
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as fh:
            json.dump(out, fh, indent=2)
    added = 0
    if not a.no_queue:
        q = queue.load(a.queue)
        added = queue.merge(q, all_f)
        queue.save(q, a.queue)
    lit = bool(repos) and len(errors) <= max(1, len(repos) // 10)
    summary = render_summary(out, added, lit)
    if a.summary:
        with open(a.summary, "a") as fh:
            fh.write(summary)
    print(summary)
    return 0 if lit else 1


def render_summary(out, added, lit):
    light = "LIT" if lit else "DARK"
    s = [f"## Lost-code scan: {light}", "",
         f"{'Scan ran cleanly.' if lit else 'Scan did not run cleanly.'} "
         f"Repos scanned: {len(out['repos'])}, failed: {len(out['errors'])}, "
         f"findings: {len(out['findings'])}, new in queue: {added}, API calls: {out['api_calls']} "
         f"(remaining {out['rate_remaining']}), {out['seconds']}s.", ""]
    if out["findings"]:
        s += ["| conf | repo | kind | where | missing |", "|---|---|---|---|---|"]
        for f in sorted(out["findings"], key=lambda f: -f["confidence"])[:40]:
            s.append(f"| {f['confidence']} | {f['repo']} | {f['kind']} | `{f['file']}:{f['line']}` | `{f['path']}` |")
    if out["errors"]:
        s += ["", "Failed repos:"] + [f"- {k}: {v}" for k, v in out["errors"].items()]
    return "\n".join(s) + "\n"


def cmd_list(a):
    q = queue.load(a.queue)
    for it in q["items"].values():
        if a.state and it["state"] != a.state:
            continue
        f = it["finding"]
        print(f"{it['id']}  {it['state']:<14} {f.get('confidence', 0):<5} {f['repo']}  {f['file']}:{f['line']} -> {f['path']}")


def cmd_state(a, state):
    q = queue.load(a.queue)
    queue.set_state(q, a.id, state, a.by, a.note or "")
    queue.save(q, a.queue)
    print(f"{a.id} -> {state}")


def cmd_fix(a):
    q = queue.load(a.queue)
    items = [q["items"][a.id]] if a.id else [i for i in q["items"].values() if fixer.eligible(i)]
    for it in items:
        if not fixer.eligible(it):
            print(f"skip {it['id']}: needs state verified and confidence >= {fixer.MIN_CONFIDENCE}")
            continue
        r = fixer.prepare(it, apply=a.apply)
        print(json.dumps({k: v for k, v in r.items() if k != "pr_body"}, indent=2))
        print(r["pr_body"], "\n---")
        if a.apply and r["applied"]:
            it["fork_branch"] = r["branch"]
            it["pr_body"] = r["pr_body"]
            queue.set_state(q, it["id"], "fixed-on-fork", "fixer")
    queue.save(q, a.queue)


def cmd_send(a):
    q = queue.load(a.queue)
    it = q["items"][a.id]
    try:
        sender.check(it)
    except sender.NotApproved as e:
        print(f"refusing to send: {e}")
        return 2
    if not a.yes:
        print("approved; re-run with --yes to open the upstream PR")
        return 0
    url = sender.send(it)
    queue.set_state(q, a.id, "sent", a.by, url)
    queue.save(q, a.queue)
    print(url)


def main(argv=None):
    p = argparse.ArgumentParser(prog="lostcode")
    p.add_argument("--queue", default=QUEUE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan repos for lost code")
    s.add_argument("--top", type=int, default=0, help="scan the top N starred code repos")
    s.add_argument("--repo", action="append", help="owner/name (repeatable)")
    s.add_argument("--out", help="write findings JSON here")
    s.add_argument("--summary", help="append a markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    s.add_argument("--lookups", type=int, default=25, help="history API lookups per repo")
    s.add_argument("--max-per-repo", type=int, default=40)
    s.add_argument("--min-confidence", type=float, default=0.5)
    s.add_argument("--min-remaining", type=int, default=50, help="stop API use below this many calls left")
    s.add_argument("--no-queue", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--state")
    for name in ("verify", "reject", "approve"):
        x = sub.add_parser(name); x.add_argument("id"); x.add_argument("--by", required=(name == "approve"), default="joshua")
        x.add_argument("--note")
    f = sub.add_parser("fix", help="prepare a fork branch (dry-run unless --apply)")
    f.add_argument("id", nargs="?"); f.add_argument("--apply", action="store_true")
    se = sub.add_parser("send", help="open the upstream PR for an APPROVED finding (manual)")
    se.add_argument("id"); se.add_argument("--yes", action="store_true"); se.add_argument("--by", default="joshua")
    a = p.parse_args(argv)
    if a.cmd == "scan":
        if not a.top and not a.repo:
            p.error("give --top N or --repo owner/name")
        return cmd_scan(a)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd in ("verify", "reject", "approve"):
        return cmd_state(a, {"verify": "verified", "reject": "rejected", "approve": "approved"}[a.cmd])
    if a.cmd == "fix":
        return cmd_fix(a)
    if a.cmd == "send":
        return cmd_send(a)


if __name__ == "__main__":
    sys.exit(main())
