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
| Isolate | `new-feature` | A worktree and branch per task. Parallel agents stop colliding. |
| Build | `code-structure` | Actions own the why, services own the how. One fix propagates everywhere. |
| Prove | `evidence-driven-testing` | A recording of the test being run, annotated and attached. |
| Ship | `before-and-after`, `greploop` | Before/after table in the PR, iterated to a clean review. |

`unslop` runs across everything a person will read, at every beat.

## All ten skills

### Workflow

- **[new-feature](skills/new-feature/SKILL.md)** - Start every task in a Git
  worktree branched from `origin/main`. Covers unique naming, a scope check
  against open PRs, fresh dependency installs, and cleanup after merge.
  Includes harness deltas for Claude Code and Cursor, which manage worktrees
  themselves.

- **[code-structure](skills/code-structure/SKILL.md)** - Service layer
  architecture. Actions orchestrate domain rules, a service layer centralizes
  reusable mechanics. Ships a migration checklist for extracting shared logic
  safely and a table of anti-patterns (god services, leaky services,
  over-abstraction).

- **[evidence-driven-testing](skills/evidence-driven-testing/SKILL.md)** - The
  agent drives the app live via computer use while a bundled recorder captures
  the session, timestamps each assertion, burns them into `evidence.mp4`, and
  posts the video plus a summary to the PR. Headless environments fall back to
  scripted screenshots; non-UI changes still produce evidence as measured
  numbers and output pairs.

### Shipping

- **[before-and-after](skills/before-and-after/SKILL.md)** - Drives the
  `@vercel/before-and-after` CLI to produce a PR-ready `| Before | After |`
  table from two URLs, two images, or a mix.

- **[greploop](skills/greploop/SKILL.md)** - Iterates a PR, MR, or shelved
  changelist until Greptile gives 5/5 confidence with zero unresolved comments.
  Triggers the review, fixes actionable comments, resolves threads, pushes,
  repeats, up to `--max-iterations` (default 10).

- **[greploop-apps](skills/greploop-apps/SKILL.md)** - The same loop, triggered
  by tagging `@greptile-apps`, which bypasses the file-count limit that makes
  Greptile refuse huge PRs. Use when greploop gets "Too many files changed for
  review".

### Craft

- **[unslop](skills/unslop/SKILL.md)** - Cuts AI tells from anything a person
  will read. Names 31 patterns (puffery, filler, hedging, chatbot phrases, em
  dashes, colons as connectors, bold and emoji overuse, abstract metaphor
  nouns, passive voice) and applies them as a four-step loop.

- **[skill-forge](skills/skill-forge/SKILL.md)** - Write and audit skills that
  actually load. Covers trigger-focused descriptions, the frontmatter fields
  that matter, the layout the CLI discovers, and a debugging order for a skill
  that never fires.

- **[release-train](skills/release-train/SKILL.md)** - Cut and publish a
  versioned release. Semver rules specific to skills, a pre-publish audit, npm
  scoped publishing, GitHub releases, and what rollback actually looks like
  when npm will not let you republish a version.

- **[windows-shell](skills/windows-shell/SKILL.md)** - Commands that run on
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
npx skills add aditya-deokar/software-factory --skill new-feature --skill unslop

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
npx skills use aditya-deokar/software-factory@unslop | claude
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

The root MIT license covers the packaging, `scripts/`, the docs, and the skills
authored here. Vendored skills keep their upstream licenses in their own
folders. `before-and-after` is PolyForm Shield 1.0.0, which is source-available
rather than open source.

Three skills (`code-structure`, `evidence-driven-testing`, `new-feature`) came
from a repository with no license file and need their provenance resolved
before publishing. [NOTICE.md](NOTICE.md) explains the options.

## Credits

Built on work by [michaelshimeles](https://github.com/michaelshimeles/skills),
[vercel-labs](https://github.com/vercel-labs/before-and-after),
[greptileai](https://github.com/greptileai/skills), and
[cursor](https://github.com/cursor/plugins).
