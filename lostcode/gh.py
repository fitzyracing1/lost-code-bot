"""Tiny GitHub REST client (GITHUB_TOKEN only) with rate-limit respect, and blobless clones."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
NON_CODE = ("awesome", "book", "interview", "roadmap", "free-programming", "public-apis", "developer-roadmap",
            "system-design", "coding-interview", "prompts", "cheatsheet", "guide", "tutorial", "-101", "papers")


class RateLimited(Exception):
    pass


class GitHub:
    def __init__(self, token=None, min_remaining=50):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.min_remaining = min_remaining
        self.remaining = None
        self.calls = 0

    def get(self, path, params=None):
        url = path if path.startswith("http") else API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "X-GitHub-Api-Version": "2022-11-28",
                                                   "User-Agent": "lost-code-bot"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        search = "/search/" in url
        if not search and self.remaining is not None and self.remaining < self.min_remaining:
            raise RateLimited(f"only {self.remaining} API calls left; stopping politely")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    self.calls += 1
                    if not search:  # search has its own small per-minute bucket; track the core budget
                        self.remaining = int(r.headers.get("X-RateLimit-Remaining", "999"))
                    return json.loads(r.read() or b"null")
            except urllib.error.HTTPError as e:
                if not search:
                    self.remaining = int(e.headers.get("X-RateLimit-Remaining", "999") or 999)
                if e.code in (403, 429) and (self.remaining == 0 or e.headers.get("Retry-After")):
                    wait = int(e.headers.get("Retry-After") or 0)
                    if wait and wait <= 60:
                        time.sleep(wait)
                        continue
                    raise RateLimited(f"GitHub rate limit hit ({e.code})")
                if e.code == 404 or e.code == 409 or e.code == 422:
                    return None
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        return None

    def top_repos(self, n):
        out = []
        page = 1
        while len(out) < n and page <= 5:
            res = self.get("/search/repositories", {"q": "stars:>30000", "sort": "stars", "order": "desc",
                                                     "per_page": 100, "page": page}) or {}
            for r in res.get("items", []):
                name = r["full_name"].lower()
                if r.get("archived") or not r.get("language") or any(w in name for w in NON_CODE):
                    continue
                out.append(r["full_name"])
            page += 1
            if not res.get("items"):
                break
        return out[:n]

    def last_commit_touching(self, repo, path, branch=None):
        params = {"path": path, "per_page": 1}
        if branch:
            params["sha"] = branch
        res = self.get(f"/repos/{repo}/commits", params)
        if res:
            c = res[0]
            return {"sha": c["sha"], "parent": (c.get("parents") or [{}])[0].get("sha"),
                    "message": (c["commit"]["message"] or "").splitlines()[0][:120],
                    "date": c["commit"]["committer"]["date"]}
        return None


def clone(repo, dest, token=None):
    """Depth-1 blobless clone; sparse-checkout only the files the detectors read."""
    url = f"https://github.com/{repo}.git"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    run = lambda *a: subprocess.run(a, check=True, capture_output=True, text=True, env=env, timeout=900)
    run("git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout", url, dest)
    run("git", "-C", dest, "sparse-checkout", "set", "--no-cone",
        "*.md", "*.markdown", "package.json", "pyproject.toml", "/.github/workflows/*",
        "docusaurus.config.*", "mkdocs.yml", "book.toml")
    run("git", "-C", dest, "checkout", "--quiet")
    head = run("git", "-C", dest, "rev-parse", "HEAD").stdout.strip()
    branch = run("git", "-C", dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    files = run("git", "-C", dest, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    return head, branch, files


def log(*a):
    print(*a, file=sys.stderr, flush=True)
