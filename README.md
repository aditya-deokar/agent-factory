# Software Factory

Agent skills for shipping software with proof. Ten skills that take a task from
an isolated branch to a merged PR with evidence attached, wired together by a
single `AGENTS.md` workflow.

Works with Claude Code, Cursor, Codex, GitHub Copilot, OpenCode, Windsurf, and
[75 more agents](https://github.com/vercel-labs/skills#supported-agents).

```bash
npx skills add aditya-deokar/software-factory
```

## Why this exists

An agent that says "I fixed it and tested it" has told you nothing you can
check. These skills replace that claim with artifacts: a branch that cannot
collide with another agent's, a screenshot pair in the PR body, a recorded
session showing the test being performed, and a review score that has to reach
5/5 before merge.

Longer version in [docs/BENEFITS.md](docs/BENEFITS.md).

## The four beats

Every task moves through the same shape. [`AGENTS.md`](AGENTS.md) is the file
that enforces it; drop it into any repo alongside the skills.

| Beat | Skill | What you get |
|---|---|---|
| Isolate | `worktree-isolation` | A worktree and branch per task. Parallel agents stop colliding. |
| Build | `service-layer` | Actions own the why, services own the how. One fix propagates everywhere. |
| Prove | `test-evidence` | A recording of the test being run, annotated and attached. |
| Ship | `visual-diff`, `code-review-loop` | Before/after table in the PR, iterated to a clean review. |

`prose-cleanup` runs across everything a person will read, at every beat.

## All ten skills

### Workflow

- **[worktree-isolation](skills/worktree-isolation/SKILL.md)** - A branch alone does not
  isolate anything; two agents in one checkout interleave edits regardless. Sets
  up a worktree per task, checks for overlap with work already in flight before
  starting, and covers what worktrees do *not* isolate: ports, databases,
  lockfiles, global config.

- **[service-layer](skills/service-layer/SKILL.md)** - Two questions decide
  where code lives: would it change if the product rules changed, or if the
  vendor changed. Boundaries own the first, services own the second. Ships an
  ordered extraction procedure you can stop partway through, and the five ways
  it usually goes wrong.

- **[test-evidence](skills/test-evidence/SKILL.md)** -
  Replace "I tested it and it works" with an artifact. The bundled recorder
  (`scripts/record.py`) captures the session while the agent drives the app,
  burns timestamped pass/fail annotations into `evidence.mp4`, and writes a
  report. Headless environments fall back to scripted screenshots; changes with
  no visible surface still produce evidence as measured numbers and output
  pairs.

### Shipping

- **[visual-diff](skills/visual-diff/SKILL.md)** - Drives the
  `@vercel/before-and-after` CLI to produce a PR-ready `| Before | After |`
  table from two URLs, two images, or a mix.

- **[code-review-loop](skills/code-review-loop/SKILL.md)** - Iterates a PR, MR, or shelved
  changelist until Greptile gives 5/5 confidence with zero unresolved comments.
  Triggers the review, fixes actionable comments, resolves threads, pushes,
  repeats, up to `--max-iterations` (default 10).

- **[code-review-loop-large](skills/code-review-loop-large/SKILL.md)** - The same loop, triggered
  by tagging `@greptile-apps`, which bypasses the file-count limit that makes
  Greptile refuse huge PRs. Use when code-review-loop gets "Too many files changed for
  review".

### Craft

- **[prose-cleanup](skills/prose-cleanup/SKILL.md)** - Cuts AI tells from anything a person
  will read. Names 31 patterns (puffery, filler, hedging, chatbot phrases, em
  dashes, colons as connectors, bold and emoji overuse, abstract metaphor
  nouns, passive voice) and applies them as a four-step loop.

- **[skill-authoring](skills/skill-authoring/SKILL.md)** - Write and audit skills that
  actually load. Covers trigger-focused descriptions, the frontmatter fields
  that matter, the layout the CLI discovers, and a debugging order for a skill
  that never fires.

- **[package-release](skills/package-release/SKILL.md)** - Cut and publish a
  versioned release. Semver rules specific to skills, a pre-publish audit, npm
  scoped publishing, GitHub releases, and what rollback actually looks like
  when npm will not let you republish a version.

- **[cross-platform-shell](skills/cross-platform-shell/SKILL.md)** - Commands that run on
  Windows. PowerShell 5.1 traps, a POSIX translation table, path and
  line-ending rules, and why `npx skills add --copy` is the fix when symlinks
  fail.

## Install

```bash
# Everything, into the project
npx skills add aditya-deokar/software-factory

# Everything, available in every project
npx skills add aditya-deokar/software-factory --global

# Pick specific skills
npx skills add aditya-deokar/software-factory --skill worktree-isolation --skill prose-cleanup

# Target specific agents
npx skills add aditya-deokar/software-factory -a claude-code -a cursor

# See what is in here without installing
npx skills add aditya-deokar/software-factory --list
```

On Windows, symlinking needs Developer Mode or an elevated shell. If install
fails, add `--copy`.

From npm, if you prefer a pinned version:

```bash
npm install --save-dev @software-factory/skills
npx skills add ./node_modules/@software-factory/skills
```

Full walkthrough in [docs/USAGE.md](docs/USAGE.md).

## Use one without installing

```bash
npx skills use aditya-deokar/software-factory@prose-cleanup | claude
```

## Documentation

| Doc | What is in it |
|---|---|
| [docs/ROADMAP.md](docs/ROADMAP.md) | The publishing plan. Six phases from empty repo to a listed, versioned package. |
| [docs/USAGE.md](docs/USAGE.md) | How to use these skills in a real project, with worked examples. |
| [docs/BENEFITS.md](docs/BENEFITS.md) | What each skill is worth, and where the value does not show up. |
| [NOTICE.md](NOTICE.md) | Origin and license of every skill. Read before publishing. |
| [AGENTS.md](AGENTS.md) | The workflow file. Copy into any repo. |

## Development

```bash
node scripts/lint-skills.mjs        # validate frontmatter, layout, paths
node scripts/lint-skills.mjs --strict   # warnings fail too
npx skills add . --list             # confirm the CLI discovers everything
python -m pytest tests/ -q          # recorder smoke tests
npm pack --dry-run                  # inspect the published tarball
```

The linter runs on `prepublishOnly`, so a broken skill cannot reach npm.

## Licensing

Seven skills are original work under the root MIT license, which also covers the
packaging, `scripts/`, and the docs. Three are vendored and keep their upstream
licenses in their own folders: `code-review-loop` and `code-review-loop-large` (MIT, Greptile),
`prose-cleanup` (MIT, Cursor), and `visual-diff` (PolyForm Shield 1.0.0, Vercel
Labs, which is source-available rather than open source).

[NOTICE.md](NOTICE.md) records the origin of every skill and what was changed
from upstream.

## Credits

`visual-diff` from [vercel-labs](https://github.com/vercel-labs/before-and-after),
`code-review-loop` from [greptileai](https://github.com/greptileai/skills), `prose-cleanup`
from [cursor](https://github.com/cursor/plugins). The subject matter of
`service-layer`, `worktree-isolation`, and `test-evidence` was prompted by
[michaelshimeles/skills](https://github.com/michaelshimeles/skills); the skills
here were written from scratch.
