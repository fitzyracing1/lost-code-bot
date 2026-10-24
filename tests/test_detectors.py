from lostcode import detect


def refs(fs):
    return sorted(f.ref for f in fs)


def test_ci_missing(tree):
    fs = detect.detect_ci(tree("ci_missing"), "o/r")
    assert refs(fs) == ["scripts/run-e2e.sh"]
    assert fs[0].line == 6 and fs[0].kind == "ci"


def test_filter_working_directory(tree):
    fs = detect.detect_ci(tree("filter_workdir"))
    # scripts/build.js exists under cli/, test/run.js exists elsewhere (suffix match) -> both filtered
    assert refs(fs) == ["scripts/gone.js"]
    assert fs[0].path == "cli/scripts/gone.js"


def test_filter_generated_earlier(tree):
    assert detect.detect_ci(tree("filter_generated")) == []


def test_filter_build_outputs(tree):
    t = tree("filter_build")
    assert detect.detect_ci(t) == []
    assert detect.detect_scripts(t) == []


def test_filter_other_repo_checkout(tree):
    assert refs(detect.detect_ci(tree("filter_checkout"))) == ["tools/mine.py"]


def test_package_scripts_and_entries(tree):
    fs = detect.detect_scripts(tree("scripts_missing"))
    got = {(f.kind, f.ref) for f in fs}
    # main src/index.js has a .ts source next to it -> not lost; bin/w.js is lost
    assert got == {("script", "scripts/lint-changed.sh"), ("script", "dbschema/seed.ts"), ("import", "bin/w.js")}
    seed = [f for f in fs if f.ref == "dbschema/seed.ts"][0]
    assert seed.path == "web/dbschema/seed.ts" and seed.line == 6


def test_pyproject(tree):
    fs = detect.detect_pyproject(tree("pyproject_missing"))
    assert sorted(f.ref for f in fs) == ["README.rst", "pkg.removed:main"]


def test_docs(tree):
    fs = detect.scan_tree(tree("docs_missing"), kinds=["doc"])
    assert refs(fs) == ["License.txt", "docs/shared-bundle-builds.md", "old/dark-theme.tsx"]
    by = {f.ref: f for f in fs}
    assert by["License.txt"].live_alternative == "LICENSE.txt"
    assert by["old/dark-theme.tsx"].live_alternative == "new/dark-theme.tsx"


def test_filter_website_links(tree):
    assert refs(detect.detect_docs(tree("filter_website"))) == ["docs/missing-file.md"]


def test_score_bounds():
    f = detect.Finding("o/r", "ci", ".github/workflows/x.yml", 1, "a.sh", "a.sh", deleted_in="abc")
    assert detect.score(f) >= 0.85
    d = detect.Finding("o/r", "doc", "examples/README.md", 1, "a.png", "a.png")
    assert detect.score(d) < 0.5
