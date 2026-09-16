# Publishing roadmap

How `@software-factory/skills` goes from a local folder to a package people can
install and a listing on skills.sh.

Everything below assumes the repo root is `C:\Users\adity\Documents\Software Factory`
and that the layout is already correct. It is; `npx skills add . --list` finds
all ten skills today.

## First, the thing that blocks publishing

Three skills came from
[michaelshimeles/skills](https://github.com/michaelshimeles/skills), which has
no LICENSE file. No license means all rights reserved. Forking on GitHub is
allowed by GitHub's terms; republishing to npm under a different name is not.

Affected: `code-structure`, `evidence-driven-testing`, `new-feature`.

You do not have to solve this before pushing to GitHub. You do have to solve it
before `npm publish`. Options are in [NOTICE.md](../NOTICE.md); the short
version is ask the author, rewrite the three files, or ship the other seven
first. Phase 3 is where this gets decided.

The other seven are clear. `greploop`, `greploop-apps`, and `unslop` are MIT
with their license files intact. `before-and-after` is PolyForm Shield, which
permits redistribution. `skill-forge`, `release-train`, and `windows-shell`
were written for this repo.

---

## Phase 0: what is already done

- [x] Old git history and the `michaelshimeles/skills` remote removed. A
      backup tarball sits in the session scratchpad if you ever want it back.
- [x] Canonical layout: `skills/<name>/SKILL.md` at the repo root, which is the
      first container directory the CLI walks.
- [x] All ten skills carry `name`, `description`, `license`, `compatibility`,
      and `metadata`. Vendored ones also carry `metadata.vendored-from`.
- [x] `scripts/lint-skills.mjs` validates the lot. 0 errors.
- [x] `npx skills add . --list` reports "Found 10 skills".
- [x] `package.json`, `LICENSE`, `NOTICE.md`, `.gitattributes`, CI workflow.
- [x] Two portability bugs fixed: `before-and-after` resolved scripts against
      the wrong directory once installed, and `new-feature` assumed `lsof`.

## Phase 1: get it into git

Nothing external yet. Local only, so mistakes are free.

```bash
cd "C:\Users\adity\Documents\Software Factory"
git init -b main
git add -A
git commit -m "Software Factory skills v1.0.0"
```

Check before moving on:

```bash
git log --stat -1        # 10 skills, no .git, no node_modules
node scripts/lint-skills.mjs
```

## Phase 2: GitHub

The repo is the primary distribution channel. `npx skills add` clones from git;
npm is the secondary path.

```bash
gh repo create aditya-deokar/software-factory --public --source=. --remote=origin
git push -u origin main
```

If you would rather not use `gh`, create the repo in the browser and:

```bash
git remote add origin https://github.com/aditya-deokar/software-factory.git
git push -u origin main
```

Then:

- [ ] Set the description to the one-liner from `package.json`.
- [ ] Add topics: `agent-skills`, `claude-code`, `cursor`, `ai-agents`, `skills`.
- [ ] Confirm the CI workflow went green. It runs the linter, checks discovery
      on Linux, macOS, and Windows, and fails if a vendored LICENSE goes
      missing from the tarball.

Verify from a clean directory, the way a stranger would:

```bash
cd "$(mktemp -d)"
npx skills add aditya-deokar/software-factory --list
```

If that prints ten skills, the GitHub half is done. Most people who install
these will never touch npm.

## Phase 3: resolve the license question

Decide now, before npm. Three paths:

**Ask.** Open an issue on michaelshimeles/skills asking for an explicit
license. Cheapest if it works. Costs you a wait of unknown length.

**Rewrite.** Copyright protects expression, not ideas. The service-layer split,
worktree-per-task, and record-evidence-while-testing are all ideas you are free
to use. Rewrite the three `SKILL.md` files from scratch without the original
open beside you. A few hours, and it ends the question permanently. This is the
one worth doing.

**Ship seven.** Set `metadata.internal: true` on the three, publish the rest,
add them back once resolved. Internal skills stay installable for you with
`INSTALL_INTERNAL_SKILLS=1` and stay hidden from everyone else.

- [ ] Path chosen and executed
- [ ] `NOTICE.md` updated to say what actually happened
- [ ] Frontmatter `license` on those three now matches reality

## Phase 4: npm

`@software-factory` is a scope, so the org has to exist first.

```bash
npm login
npm whoami                        # confirm the account
```

- [ ] Create the org at <https://www.npmjs.com/org/create>. Name it
      `software-factory`. The free tier covers unlimited public packages.
- [ ] Confirm the name is free: `npm view @software-factory/skills` should 404.

Dry run before the real thing:

```bash
npm pack --dry-run
```

Read the file list. You want `skills/` complete with every LICENSE, plus
`AGENTS.md`, `README.md`, `NOTICE.md`, `LICENSE`. You do not want `node_modules`,
`.git`, `__pycache__`, `tests/`, or `.artifacts`. The `files` array in
`package.json` already restricts this, but read it anyway.

```bash
npm publish --access public
```

`--access public` is not optional. Scoped packages default to restricted, and
without the flag the first publish fails with an error about paid plans that
sounds like a billing problem and is not one.

Verify:

```bash
npm view @software-factory/skills version
cd "$(mktemp -d)" && npm install @software-factory/skills
npx skills add ./node_modules/@software-factory/skills --list
```

Tag the release:

```bash
git tag v1.0.0 && git push --tags
gh release create v1.0.0 --title "v1.0.0" --generate-notes
```

## Phase 5: skills.sh

There is no submit form and no publish command. This surprises people.

skills.sh indexes automatically through anonymous telemetry from the CLI. When
anyone runs `npx skills add aditya-deokar/software-factory` without
`DISABLE_TELEMETRY` set, the install is counted and the repo appears in the
directory. Ranking is by aggregated install count.

Two consequences worth internalising:

1. **Your own installs count**, as long as telemetry is on and the repo is a
   confirmed-public GitHub repo. The first install that registers you is
   probably going to be yours.
2. **The repo must be public on GitHub.** The CLI only sends identifiers for
   repos GitHub confirms are public. A private repo never gets indexed.

So:

- [ ] Repo is public
- [ ] Run `npx skills add aditya-deokar/software-factory` once with telemetry on
- [ ] Wait for indexing, then check <https://skills.sh/aditya-deokar/software-factory>
- [ ] Add the badge to the README once the page exists:

```markdown
[![skills.sh](https://skills.sh/b/aditya-deokar/software-factory?style=for-the-badge)](https://skills.sh/aditya-deokar/software-factory)
```

Installs are the only ranking input, so visibility comes from people actually
using it. The honest version: write up what the workflow does somewhere people
read, and let the install count follow. There is no way to submit your way up
the leaderboard.

## Phase 6: keep it alive

Use `release-train` for every release after this one. It is in the package for
exactly this.

**Versioning**, short form: removing or renaming a skill is major, because the
folder name is the install path and a rename breaks every existing install.
Adding a skill is minor. Fixing a broken path or a wrong flag is patch. Bump
`metadata.version` on the individual skill too, so someone who installed one
skill can tell whether theirs moved.

**The audit that runs before every publish** lives in
`skills/release-train/SKILL.md`. CI covers most of it now.

**Rollback**, since npm forbids republishing a version number: unpublish works
within 72 hours if nothing depends on it, otherwise `npm deprecate` the bad
version and publish a patch. There is no overwrite, ever.

| Cadence | Task |
|---|---|
| Per change | `node scripts/lint-skills.mjs` before committing |
| Monthly | Re-read `compatibility` fields. Upstream CLIs change flags. |
| Quarterly | `npx skills add . --list` on a fresh machine. Catches drift. |
| On upstream change | Diff vendored skills against their sources, update `NOTICE.md` |

---

## The whole thing on one page

| Phase | Blocking? | Time | Output |
|---|---|---|---|
| 1. git init | no | 5 min | Local repo |
| 2. GitHub | no | 20 min | `npx skills add` works for anyone |
| 3. License | **yes, for npm** | hours to days | Clean provenance |
| 4. npm | after 3 | 30 min | `@software-factory/skills` on the registry |
| 5. skills.sh | after 2 | passive | Directory listing |
| 6. Maintenance | ongoing | - | It stays working |

Phases 1, 2, and 5 need nothing from anyone else and can be done today. Phase 4
waits on phase 3.

## Mistakes that cost the most

**Publishing before resolving the license.** npm versions cannot be
republished. A takedown request after publishing means deprecating a version
that stays in the registry forever with your name on it.

**A stale README.** Install commands with the wrong owner or package name is
the single most common broken thing in skills repos. CI does not catch it. Read
the README after every rename.

**Losing a vendored LICENSE file.** Removing it violates clause 2 of PolyForm
Shield and the MIT license terms. The `package` CI job fails the build if one
goes missing from the tarball, which is why that job exists.

**Renaming a skill folder in a minor release.** The folder name is the install
path and the handle the agent invokes. A rename is a breaking change wearing a
cosmetic disguise.

**Turning off telemetry and wondering why skills.sh is empty.** The directory
has no other input.
