"""Detectors and false-positive filters.

Everything here works on a *tree*: a set of file paths that exist on the
default branch plus a function that returns a file's text. That keeps the
detectors independent of git, so tests can feed them small fixture folders.
"""
from __future__ import annotations

import json
import posixpath
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field, asdict

import yaml

CODE_EXT = r"(?:js|mjs|cjs|ts|mts|tsx|sh|py|ps1)"
BUILD_DIRS = re.compile(r"(^|/)(dist|build|out|target|node_modules|\.next|coverage|_site|site|public/build|lib-cov)(/|$)")
SKIP_DIRS = {"node_modules", "third_party", "vendor", "external", "testdata", "fixtures",
             "__tests__", "i18n", "locales", "translations"}
SITE_CONFIGS = ("docusaurus.config.js", "docusaurus.config.ts", "mkdocs.yml", "_config.yml",
                "astro.config.mjs", "vitepress", "book.toml", "conf.py", "hugo.toml", "config.toml")


@dataclass
class Finding:
    repo: str
    kind: str            # ci | script | import | doc
    file: str            # file holding the reference
    line: int
    ref: str             # reference as written
    path: str            # resolved repo-relative path that is missing
    context: str = ""
    evidence: list = field(default_factory=list)
    recovery_sha: str | None = None
    deleted_in: str | None = None
    live_alternative: str | None = None
    confidence: float = 0.0
    head_sha: str | None = None

    def to_dict(self):
        return asdict(self)


class Tree:
    def __init__(self, paths, reader):
        self.files = set(paths)
        self.dirs = set()
        for f in self.files:
            p = f
            while "/" in p:
                p = p.rsplit("/", 1)[0]
                self.dirs.add(p)
        self.read = reader
        self._suffixes = None
        self._by_base = None

    def exists(self, p):
        p = p.strip("/")
        return p == "" or p in self.files or p in self.dirs

    def suffix_exists(self, p):
        """True if any file/dir in the repo ends with path p (working-directory filter)."""
        if self._suffixes is None:
            s = set()
            for a in self.files | self.dirs:
                parts = a.split("/")
                for i in range(len(parts)):
                    s.add("/".join(parts[i:]))
            self._suffixes = s
        return p.strip("/") in self._suffixes

    def by_basename(self, name):
        if self._by_base is None:
            d = {}
            for f in self.files:
                d.setdefault(posixpath.basename(f), []).append(f)
            self._by_base = d
        return self._by_base.get(name, [])

    def has_site(self):
        return any(posixpath.basename(f) in SITE_CONFIGS or f.endswith(SITE_CONFIGS) for f in self.files)


def norm(base_dir, ref):
    ref = urllib.parse.unquote(ref.split("#")[0].split("?")[0]).strip()
    if ref.startswith("/"):
        return posixpath.normpath(ref.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, ref)) if base_dir else posixpath.normpath(ref)


def is_build_output(p):
    return bool(BUILD_DIRS.search(p))


def _skip_file(f):
    parts = f.split("/")
    return any(x in SKIP_DIRS for x in parts[:-1])


# ---------------------------------------------------------------- CI workflows
PATH_IN_RUN = re.compile(r"(?:^|[\s\"'=(])((?:\./)?(?:[\w@.-]+/)+[\w.-]+\." + CODE_EXT + r")(?=$|[\s\"';&|)])")
WRITES = [re.compile(r">>?\s*([\w./@-]+)"), re.compile(r"\btee\s+(?:-a\s+)?([\w./@-]+)"),
          re.compile(r"\b(?:cp|mv|ln\s+-s\w*|install)\s+(?:-\w+\s+)*\S+\s+([\w./@-]+)"),
          re.compile(r"\btouch\s+([\w./@-]+)"), re.compile(r"(?:-o|--output|--outfile|--out-file)[\s=]+([\w./@-]+)"),
          re.compile(r"\b(?:curl|wget)\b.*?(?:-o|-O|--output)\s+([\w./@-]+)")]


def _generated(text):
    out = set()
    for rx in WRITES:
        for m in rx.findall(text):
            out.add(posixpath.normpath(m.lstrip("./") if m.startswith("./") else m))
    return out


def _line_of(lines, needle, start=0):
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return i + 1
    return 0


def detect_ci(tree, repo=""):
    found = []
    for wf in sorted(f for f in tree.files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))):
        text = tree.read(wf)
        try:
            doc = yaml.safe_load(text) or {}
        except Exception:
            continue
        lines = text.splitlines()
        jobs = doc.get("jobs") or {}
        wf_wd = (((doc.get("defaults") or {}).get("run") or {}).get("working-directory")) or ""
        for jname, job in jobs.items():
            if not isinstance(job, dict):
                continue
            job_wd = (((job.get("defaults") or {}).get("run") or {}).get("working-directory")) or wf_wd
            generated, foreign = set(), []
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                uses = str(step.get("uses") or "")
                w = step.get("with") or {}
                if uses.startswith("actions/checkout") and w.get("repository") and \
                        str(w.get("repository")).split("/")[-1] not in ("", "${{ github.repository }}"):
                    if "github.repository" not in str(w.get("repository")):
                        foreign.append(str(w.get("path") or "").strip("./") or "*")
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                wd = str(step.get("working-directory") or job_wd or "")
                generated |= {norm(wd, g) for g in _generated(run)} | _generated(run)
                for raw in PATH_IN_RUN.findall(run):
                    if "${{" in raw or "$" in raw or "*" in raw:
                        continue
                    p = norm(wd if "${{" not in wd else "", raw)
                    reason = filter_reason(tree, p, raw, generated=generated, foreign=foreign)
                    if reason:
                        continue
                    ln = _line_of(lines, raw)
                    found.append(Finding(repo, "ci", wf, ln, raw, p, context=f"job {jname}",
                                         evidence=[f"{wf} job '{jname}' runs '{raw}' (working-directory: {wd or '.'})"]))
    return found


def filter_reason(tree, p, raw, generated=(), foreign=(), allow_suffix=True):
    """Return why a missing-looking path is a false positive, or None if it is really missing."""
    if tree.exists(p):
        return "exists"
    if is_build_output(p) or is_build_output(raw):
        return "build-output"
    if p in generated or raw.lstrip("./") in generated or any(p.endswith("/" + g) for g in generated):
        return "generated-earlier"
    for f in foreign:
        if f == "*" or p == f or p.startswith(f + "/") or raw.lstrip("./").startswith(f + "/"):
            return "other-repo-checkout"
    if allow_suffix and tree.suffix_exists(raw.lstrip("./").lstrip("/")):
        return "relative-to-working-directory"
    return None


# ------------------------------------------------------- package.json scripts
SCRIPT_TOK = re.compile(r"(?:^|[\s\"'=])(\.{0,2}/?(?:[\w@.-]+/)*[\w.-]+\." + CODE_EXT + r")(?=$|[\s\"';&|)])")
RUNNERS = r"(?:node|bash|sh|zsh|python3?|tsx|ts-node|bun|bun run|deno run|npx tsx|esno|vite-node)"


def detect_scripts(tree, repo=""):
    found = []
    for pj in sorted(f for f in tree.files if posixpath.basename(f) == "package.json" and not _skip_file(f)):
        try:
            text = tree.read(pj)
            data = json.loads(text)
        except Exception:
            continue
        base = posixpath.dirname(pj)
        lines = text.splitlines()
        scripts = data.get("scripts") or {}
        generated = set()
        for v in scripts.values():
            if isinstance(v, str):
                generated |= {norm(base, g) for g in _generated(v)}
        for k, v in scripts.items():
            if not isinstance(v, str):
                continue
            for m in SCRIPT_TOK.findall(v):
                if "*" in m or m.startswith("-") or "$" in m:
                    continue
                if "/" not in m and not re.search(RUNNERS + r"\s+(?:\S+\s+)*?" + re.escape(m), v):
                    continue
                p = norm(base, m)
                if filter_reason(tree, p, m, generated=generated, allow_suffix=False):
                    continue
                found.append(Finding(repo, "script", pj, _line_of(lines, f'"{k}"'), m, p, context=f"script {k}",
                                     evidence=[f'{pj} script "{k}": {v}']))
        # manifest entry points ("imports" other packages resolve to)
        for key in ("main", "module", "types", "typings"):
            v = data.get(key)
            if isinstance(v, str):
                found += _entry(tree, repo, pj, base, lines, key, v)
        b = data.get("bin")
        if isinstance(b, str):
            found += _entry(tree, repo, pj, base, lines, "bin", b)
        elif isinstance(b, dict):
            for k, v in b.items():
                if isinstance(v, str):
                    found += _entry(tree, repo, pj, base, lines, f"bin.{k}", v)
    return found


def _entry(tree, repo, pj, base, lines, key, v):
    p = norm(base, v)
    if is_build_output(p) or tree.exists(p):
        return []
    for ext in (".js", ".ts", ".mjs", ".cjs", "/index.js", "/index.ts", ".d.ts"):
        if tree.exists(p + ext):
            return []
    # compiled outputs: main points to .js but .ts source sits next to it
    stem = re.sub(r"\.(c|m)?js$", "", p)
    if stem != p and any(tree.exists(stem + e) for e in (".ts", ".tsx", ".mts")):
        return []
    if re.search(r"\.d\.ts$", p):
        return []
    return [Finding(repo, "import", pj, _line_of(lines, f'"{key.split(".")[0]}"'), v, p, context=f"package.json {key}",
                    evidence=[f'{pj} "{key}" points to {v}'])]


# --------------------------------------------------------- pyproject scripts
def _py_module_exists(tree, base, mod):
    rel = mod.replace(".", "/")
    for root in ("", "src/", "lib/", "python/"):
        for cand in (rel + ".py", rel + "/__init__.py", rel):
            if tree.exists(posixpath.join(base, root + cand)):
                return True
    return tree.suffix_exists(rel + ".py") or tree.suffix_exists(rel + "/__init__.py")


def detect_pyproject(tree, repo=""):
    found = []
    for pp in sorted(f for f in tree.files if posixpath.basename(f) == "pyproject.toml" and not _skip_file(f)):
        try:
            text = tree.read(pp)
            data = tomllib.loads(text)
        except Exception:
            continue
        base = posixpath.dirname(pp)
        lines = text.splitlines()
        proj = data.get("project") or {}
        poetry = ((data.get("tool") or {}).get("poetry") or {})
        tables = [("project.scripts", proj.get("scripts") or {}), ("project.gui-scripts", proj.get("gui-scripts") or {}),
                  ("tool.poetry.scripts", poetry.get("scripts") or {})]
        for tname, table in tables:
            for name, target in table.items():
                if not isinstance(target, str) or ":" not in target:
                    continue
                mod = target.split(":")[0].strip()
                if _py_module_exists(tree, base, mod):
                    continue
                found.append(Finding(repo, "import", pp, _line_of(lines, name), target, posixpath.join(base, mod.replace(".", "/") + ".py"),
                                     context=f"{tname}.{name}", evidence=[f"{pp} [{tname}] {name} = {target!r}: module {mod} not found"]))
        # file references (readme, license-files) in [project]
        rd = proj.get("readme")
        if isinstance(rd, str):
            p = norm(base, rd)
            if not tree.exists(p):
                found.append(Finding(repo, "import", pp, _line_of(lines, "readme"), rd, p, context="project.readme",
                                     evidence=[f"{pp} readme = {rd!r} missing"]))
    return found


# ------------------------------------------------------------------ doc links
MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
HREF = re.compile(r"(?:href|src)=\"([^\"]+)\"")
CODE_FENCE = re.compile(r"^\s*(```|~~~)")


def detect_docs(tree, repo=""):
    found = []
    site = tree.has_site()
    for md in sorted(f for f in tree.files if f.endswith((".md", ".markdown")) and not _skip_file(f)):
        try:
            text = tree.read(md)
        except Exception:
            continue
        if text is None or len(text) > 500_000:
            continue
        base = posixpath.dirname(md)
        fence = False
        for i, line in enumerate(text.splitlines(), 1):
            if CODE_FENCE.match(line):
                fence = not fence
                continue
            if fence:
                continue
            for m in MD_LINK.findall(line) + HREF.findall(line):
                if re.match(r"^([a-z][a-z0-9+.-]*:|#|\{|\$|<|%|//)", m, re.I) or "{{" in m or "{%" in m:
                    continue
                p = norm(base, m)
                if p.startswith(".."):
                    continue  # escapes the repo: links to a site/other repo
                if doc_filter_reason(tree, p, m, site):
                    continue
                found.append(Finding(repo, "doc", md, i, m, p, context=line.strip()[:200],
                                     evidence=[f"{md}:{i} links to {m}"]))
    return found


def doc_filter_reason(tree, p, raw, site):
    if tree.exists(p):
        return "exists"
    if is_build_output(p):
        return "build-output"
    for ext in (".md", ".mdx", "/README.md", "/index.md", "/index.mdx", ".html"):
        if tree.exists(p + ext) or tree.exists(re.sub(r"\.html$", "", p) + ext):
            return "website-link"
    if raw.startswith("/") and site:
        return "website-link"  # site-root links resolve on the project's docs site
    if site and not posixpath.splitext(p)[1]:
        return "website-link"  # extensionless pretty URL on a docs site
    return None


DETECTORS = {"ci": detect_ci, "script": detect_scripts, "pyproject": detect_pyproject, "doc": detect_docs}


def scan_tree(tree, repo="", kinds=None):
    out = []
    for name, fn in DETECTORS.items():
        if kinds and name not in kinds:
            continue
        out += fn(tree, repo)
    seen, uniq = set(), []
    for f in out:
        k = (f.kind, f.file, f.ref)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    for f in uniq:
        alts = [a for a in tree.by_basename(posixpath.basename(f.path)) if a != f.path]
        if len(alts) == 1:
            f.live_alternative = alts[0]
            f.evidence.append(f"same file name exists at {alts[0]}")
        elif not alts:
            # case-only mismatch, e.g. License.txt vs LICENSE.txt
            lower = {x.lower(): x for x in tree.files}
            if f.path.lower() in lower:
                f.live_alternative = lower[f.path.lower()]
                f.evidence.append(f"case mismatch: real file is {f.live_alternative}")
    return uniq


def score(f):
    """Confidence 0..1 that a finding is real, broken and worth fixing."""
    base = {"ci": 0.7, "script": 0.65, "import": 0.6, "doc": 0.45}.get(f.kind, 0.4)
    if f.deleted_in:
        base += 0.2
    if f.live_alternative:
        base += 0.1
    if f.kind == "doc" and f.path.lower().endswith((".png", ".jpg", ".gif", ".svg")):
        base -= 0.1
    if re.search(r"(example|template|sample|demo)", f.file, re.I):
        base -= 0.1
    return round(max(0.05, min(base, 0.99)), 2)
