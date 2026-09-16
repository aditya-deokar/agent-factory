---
name: package-release
description: Cut a versioned release of a skills repo and publish it to npm and GitHub. Use when asked to publish, release, ship a new version, bump the version, or tag a release of an agent-skills package. Covers semver for skills, the pre-publish audit, npm scoped publishing, GitHub releases, and rollback.
license: MIT
compatibility: Requires Node.js 18+, npm 9+ with an authenticated account (npm whoami), git, and an authenticated gh CLI for the GitHub release step. Scoped packages need --access public on first publish or npm rejects them as private.
metadata:
  author: software-factory
  version: "1.0"
allowed-tools: Bash(npm:*) Bash(npx skills *) Bash(git:*) Bash(gh release *) Bash(node:*)
---

# Package release

Publishing a skills package is not publishing a library. Nobody imports it.
People install the files and an agent reads them, so the failure mode is not a
broken build, it is a skill that silently stops firing. The audit below exists
because of that.

## Semver for skills

Version the package on what happens to the agent, not on lines changed.

| Change | Bump |
|---|---|
| Removed a skill, or renamed one | major |
| Narrowed a description so it fires in fewer cases | major |
| Removed a step the user depended on | major |
| Added a skill | minor |
| Broadened a description | minor |
| Added a section, reference file, or script | minor |
| Fixed a typo, a broken path, a wrong flag | patch |
| Loosened `compatibility` wording with no behaviour change | patch |

A renamed skill is a major bump even though it looks cosmetic. The folder name
is the install path and the invocation handle, so a rename breaks every
existing install.

Bump the per-skill `metadata.version` too. Package version tracks the bundle;
skill version tells a user whether the one skill they installed changed.

## Pre-publish audit

Stop and fix anything that fails. Do not publish past a red line here.

1. `npx skills add . --list` lists every skill you expect, with no warnings.
2. `npm pack --dry-run` shows the `skills/` tree and no junk: no `.git`, no
   `node_modules`, no `__pycache__`, no `.artifacts`.
3. Every vendored skill still has its `LICENSE` file in its folder.
4. `NOTICE.md` (or equivalent) matches what is actually vendored. A skill added
   since the last release and missing from the notice is a licensing bug.
5. Repo tests pass, if there are any.
6. `README` install commands name the real owner and package. This is the most
   commonly stale thing in a skills repo.
7. Working tree is clean and rebased on the latest default branch.

## Publish

```bash
npm whoami                                  # confirm the right account
npm version <major|minor|patch> -m "release: v%s"
npm publish --access public                 # --access public is required for scoped packages
git push --follow-tags
gh release create "v$(node -p "require('./package.json').version")" \
  --title "v$(node -p "require('./package.json').version")" \
  --notes-file CHANGELOG-latest.md
```

`npm version` commits and tags in one step, so run it on a clean tree.
`--follow-tags` pushes the commit and its tag together; a plain `git push`
leaves the tag behind and the GitHub release then points at nothing.

Scoped packages default to restricted. Without `--access public` the first
publish fails with a paid-plan error that reads like a billing problem and is
not one.

## Verify after publishing

Do not trust a successful publish. Check the thing a user would hit.

```bash
npm view <package> version                  # registry agrees with your tag
cd "$(mktemp -d)" && npx skills add <owner>/<repo> --list
```

Install from the published source into a scratch directory and confirm the
skills list. This catches the case where the package published fine but the
repo layout means the CLI finds nothing.

## Rollback

npm forbids republishing a version number, ever. There is no overwrite.

- Within 72 hours and with no dependents, `npm unpublish <pkg>@<version>` works.
- After that, `npm deprecate <pkg>@<version> "<reason, and what to use>"`. The
  version stays installable, and anyone installing it sees the warning.
- Either way, publish a fixed patch version. That is the actual remedy.
- Delete the bad git tag and GitHub release so the history does not point at a
  version nobody should install:
  `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`

## Release notes people read

Group by skill, not by commit. A user of this package cares about one or two
skills and wants to know whether theirs moved.

```markdown
## v1.2.0

### Added
- `cross-platform-shell` - PowerShell and Git Bash portability rules.

### Changed
- `visual-diff` - scripts are now resolved from the skill directory.
  Bodies that hardcoded a relative script path stopped working after install.

### Fixed
- `worktree-isolation` - the port check assumed lsof, which Windows does not have.
```

Say what broke and why it changed. "Various improvements" tells a reader
nothing and makes them diff the package themselves.
