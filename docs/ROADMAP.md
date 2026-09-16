# Publishing roadmap

How `@software-factory/skills` goes from a local folder to a package people can
install and a listing on skills.sh.

Everything below assumes the repo root is `C:\Users\adity\Documents\Software Factory`
and that the layout is already correct. It is; `npx skills add . --list` finds
all ten skills today.

## Provenance, which is settled

Three skills (`service-layer`, `test-evidence`, `worktree-isolation`) began
as copies from [michaelshimeles/skills](https://github.com/michaelshimeles/skills),
a repository with no license file. No license means all rights reserved, so
those copies could not legally be republished.

All three have been rewritten from scratch. They share no text with the
originals, only subject matter, which is not protected. The bundled recorder was
replaced too: the vendored `evidence.py` and its test suite are gone, and
`scripts/record.py` is an independent implementation with its own session
format and tests.

Nothing from that repository remains. npm is unblocked.

The three vendored skills that stay vendored are fine as they are. `code-review-loop`,
`code-review-loop-large`, and `prose-cleanup` are MIT with their license files intact.
`visual-diff` is PolyForm Shield, which permits redistribution as long as
the notice survives, and CI fails the build if it does not.

---

## Phase 0: done

- [x] Old git history and the upstream remote removed.
- [x] Canonical layout: `skills/<name>/SKILL.md` at the repo root, the first
      container directory the CLI walks.
- [x] All ten skills carry `name`, `description`, `license`, `compatibility`,
      and `metadata`. Vendored ones also carry `metadata.vendored-from`.
- [x] `scripts/lint-skills.mjs` validates the lot. 0 errors.
- [x] `npx skills add . --list` reports "Found 10 skills".
- [x] Three skills rewritten as original work, recorder replaced.
- [x] `package.json`, `LICENSE`, `NOTICE.md`, `.gitattributes`, CI workflow.
- [x] Two portability bugs fixed: `visual-diff` resolved scripts against
      the wrong directory once installed, and `worktree-isolation` assumed `lsof`.
- [x] 26 tests passing (4 more need ffmpeg and run in CI).

## Phase 1: git

- [x] `git init`, committed on `master`.

## Phase 2: GitHub

- [x] Pushed to <https://github.com/aditya-deokar/software-factory>.

Then, in the repo settings:

- [ ] Set the description to the one-liner from `package.json`.
- [ ] Add topics: `agent-skills`, `claude-code`, `cursor`, `ai-agents`, `skills`.
- [ ] Confirm CI went green. It runs the linter, checks discovery on Linux,
      macOS, and Windows, and fails if a vendored LICENSE goes missing from the
      tarball.

Verify the way a stranger would:

```bash
cd "$(mktemp -d)"
npx skills add aditya-deokar/software-factory --list
```

Ten skills listed means the GitHub half works. Most people who install these
will never touch npm.

## Phase 3: make the repo readable

Nothing here blocks anything. It decides whether a visitor installs.

- [ ] The README's first screen has to answer "what is this and why would I
      install it" without scrolling. It currently does.
- [ ] Pin the repo on your GitHub profile.
- [ ] Consider a short demo: a recording made with `record.py` of the four
      beats running on a real task. This package is about evidence over
      assertion, and a repo that asserts its own value without showing it is
      an easy thing to notice.

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
```

Then create the release at
<https://github.com/aditya-deokar/software-factory/releases/new>, picking the
tag you just pushed. (`gh release create v1.0.0 --generate-notes` does the same
thing once the GitHub CLI is installed; it is not on this machine.)

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

Use `package-release` for every release after this one. It is in the package for
exactly this.

**Versioning**, short form: removing or renaming a skill is major, because the
folder name is the install path and a rename breaks every existing install.
Adding a skill is minor. Fixing a broken path or a wrong flag is patch. Bump
`metadata.version` on the individual skill too, so someone who installed one
skill can tell whether theirs moved.

**The audit that runs before every publish** lives in
`skills/package-release/SKILL.md`. CI covers most of it now.

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

| Phase | Status | Time left | Output |
|---|---|---|---|
| 0. Prep and rewrites | done | - | Clean provenance, 10 valid skills |
| 1. git | done | - | Repo on `master` |
| 2. GitHub | done | 10 min of settings | `npx skills add` works for anyone |
| 3. Readable repo | open | 30 min | A visitor who installs |
| 4. npm | open | 30 min | `@software-factory/skills` on the registry |
| 5. skills.sh | open | passive | Directory listing |
| 6. Maintenance | ongoing | - | It stays working |

Nothing is blocked. Phase 4 needs an npm org created by hand, which is the only
step that waits on a web form.

## Mistakes that cost the most

**Publishing before resolving provenance.** npm versions cannot be
republished. A takedown request after publishing means deprecating a version
that stays in the registry forever with your name on it. This is why the three
skills were rewritten before the first publish rather than after.

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
