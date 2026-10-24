import ast
import os

import pytest

from lostcode import fixer, queue, send

F = {"repo": "o/r", "kind": "doc", "file": "docs/README.md", "line": 3, "ref": "../License.txt",
     "path": "License.txt", "live_alternative": "LICENSE.txt", "confidence": 0.9, "evidence": ["x"]}


def test_dedupe_and_states(tmp_path):
    p = str(tmp_path / "q.json")
    q = queue.load(p)
    assert queue.merge(q, [F]) == 1
    fid = next(iter(q["items"]))
    queue.set_state(q, fid, "rejected", "joshua")
    queue.save(q, p)
    q = queue.load(p)
    assert queue.merge(q, [dict(F, line=4)]) == 0
    assert q["items"][fid]["state"] == "rejected"  # human decision survives re-scan
    assert q["items"][fid]["finding"]["line"] == 4
    with pytest.raises(ValueError):
        queue.set_state(q, fid, "bogus", "joshua")
    with pytest.raises(PermissionError):
        queue.set_state(q, fid, "approved", "bot")


def item(state="verified", **kw):
    return {"id": "abc", "state": state, "history": [{"state": state, "by": "joshua"}], "finding": dict(F, **kw)}


def test_fixer_dry_run_rewrite():
    calls = []
    r = fixer.prepare(item(), apply=False, runner=lambda *a, **k: calls.append(a))
    assert calls == [] and not r["applied"]
    assert r["plan"]["new"] == "../LICENSE.txt"
    assert r["branch"] == "try/lost-code-abc" and r["fork"] == "fitzyracing1/r"
    assert "License.txt" in r["pr_body"]


def test_fixer_restore_plan():
    it = item(live_alternative=None, recovery_sha="deadbeef" * 5)
    assert fixer.plan(it)["action"] == "restore-file"


def test_fixer_eligibility():
    assert fixer.eligible(item())
    assert not fixer.eligible(item(state="new"))
    assert not fixer.eligible(item(confidence=0.5))


def test_fixer_apply_never_touches_upstream(tmp_path):
    calls = []

    def runner(cmd, **k):
        calls.append(cmd)
        if cmd[:2] == ["git", "clone"]:
            d = cmd[-1]
            os.makedirs(os.path.join(d, "docs"))
            open(os.path.join(d, "docs/README.md"), "w").write("[l](../License.txt)")
    r = fixer.prepare(item(), apply=True, workdir=str(tmp_path / "w"), runner=runner)
    assert r["applied"]
    flat = [" ".join(c) for c in calls]
    assert not any(" pr " in c or " issue " in c or "comment" in c for c in flat)
    assert any(c.startswith("git -C") and "push" in c and "origin try/lost-code-abc" in c for c in flat)
    assert open(tmp_path / "w/docs/README.md").read() == "[l](../LICENSE.txt)"


def test_fixer_source_has_no_upstream_calls():
    src = open(fixer.__file__).read()
    tree = ast.parse(src)
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not ({"pr", "issue", "api"} & strings)


def test_send_requires_human_approval():
    with pytest.raises(send.NotApproved):
        send.check(item(state="fixed-on-fork"))
    it = item(state="approved")
    it["history"][-1]["by"] = "bot"
    it["fork_branch"] = "try/lost-code-abc"
    with pytest.raises(send.NotApproved):
        send.check(it)
    it["history"][-1]["by"] = "joshua"
    send.check(it)
    calls = []
    send.send(it, runner=lambda c, **k: calls.append(c) or type("R", (), {"stdout": "url"})())
    assert calls[0][:3] == ["gh", "pr", "create"]
