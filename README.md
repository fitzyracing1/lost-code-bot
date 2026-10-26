# lost-code-bot

Big projects move and delete files. Sometimes something still points at the old file: a CI step, an npm script, a `pyproject.toml` entry point, or a link in the docs. We call that **lost code**. This bot looks for it in the most-starred public repos on GitHub.

## What it checks

- **CI steps** in `.github/workflows` that run a file that isn't there.
- **package.json** scripts, `main` and `bin`, and **pyproject.toml** script entry points and `readme`, when they point at missing files or modules.
- **Doc links** inside the repo (Markdown links and `href`/`src`) that would 404.

For each hit it asks GitHub for the last commit that touched the path. That commit is usually the one that deleted the file. Its parent is the **recovery SHA**, the last commit where the file still existed.

## How it avoids false alarms

It skips a hit when the path:
- is a build output (`dist/`, `build/`, `.next/`, `node_modules/` and so on),
- is written earlier in the same CI job (`>`, `cp`, `tee`, `curl -o`, ...),
- exists somewhere in the repo when you allow for the job's `working-directory`,
- belongs to a different repo that the job checks out (`actions/checkout` with `repository:`),
- is a website link, meaning a site-root or extensionless link in a repo that has a docs site, or a link that resolves once `.md` or `/index.md` is added.

Each finding gets a **confidence** score from 0 to 1. CI hits score higher than doc hits. A known deleting commit or a file with the same name elsewhere in the repo raises the score.

## Use it

```sh
pip install -e .
export GITHUB_TOKEN=...            # any token; the daily job uses Actions' GITHUB_TOKEN
lostcode scan --repo owner/name --out findings/run.json
lostcode scan --top 50            # top 50 starred code repos (skips awesome-lists, books, etc.)
lostcode list --state new
lostcode verify <id>              # a human checked it
lostcode reject <id>
```

`./scanner/lostcode` runs the same CLI without installing it.

## The queue

`findings/queue.json` stores every finding along with its state:

`new` → `verified` → `fixed-on-fork` → `approved` → `sent`  (or `rejected` at any point)

Each finding's ID comes from repo + kind + file + reference, so scanning again never adds a duplicate. A re-scan also never overwrites a state that a person set. The queue started with 12 findings that were checked by hand in the 2026-10-24 audit.

## Fixing and sending (two separate steps)

1. `lostcode fix [<id>]` is a **dry run** by default. For `verified` findings with confidence ≥ 0.85, it shows the planned change, which is either pointing the reference at the file's new location or restoring the file from the recovery SHA, along with a drafted PR body. Adding `--apply` forks the repo to `fitzyracing1`, pushes a throwaway branch `try/lost-code-<id>` **to the fork only**, and moves the finding to `fixed-on-fork`. The fixer never opens PRs, issues or comments upstream, and a test checks that.
2. `lostcode approve <id> --by joshua` is done by Joshua alone. After that, `lostcode send <id> --yes` opens the upstream PR. `send` refuses to run unless the finding is `approved`, the approval came from a person, and a fork branch exists.

## Automation

- `.github/workflows/ci.yml` runs the tests on every push.
- `.github/workflows/scan.yml` runs every day and can also be started by hand. It scans the top ~50 repos using only `GITHUB_TOKEN`, keeps API use low, and stops early if the remaining budget drops too far. It then commits any queue changes and writes a summary with a light: **LIT** means the scan ran cleanly, **DARK** means it didn't (more than 10% of repos failed, or nothing was scanned).

## License

MIT, Copyright (c) 2026 Joshua Almeida.

## Plans

This project is free, and its license stays as is. Support it through [GitHub Sponsors](https://github.com/sponsors/fitzyracing1):

- **Free**: use the code, community support.
- **Pro ($49/month)**: priority issues and support.
- **Team ($199/month)**: priority support for your team plus early access.
