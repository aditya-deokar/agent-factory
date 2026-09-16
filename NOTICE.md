# Notice and attribution

`@software-factory/skills` bundles skills from several sources. Each one keeps
its upstream license, which is included in the skill's own folder. The MIT
license at the repo root covers only the packaging, the tooling in `scripts/`,
the documentation, and the skills authored here.

Read this file before publishing, and update it whenever a skill is added.

## Origin of every skill

| Skill | Origin | License | License file |
|---|---|---|---|
| `before-and-after` | [vercel-labs/before-and-after](https://github.com/vercel-labs/before-and-after) | PolyForm Shield 1.0.0 | `skills/before-and-after/LICENSE` |
| `code-structure` | [michaelshimeles/skills](https://github.com/michaelshimeles/skills) | see "Unresolved" below | none upstream |
| `evidence-driven-testing` | [michaelshimeles/skills](https://github.com/michaelshimeles/skills) | see "Unresolved" below | none upstream |
| `greploop` | [greptileai/skills](https://github.com/greptileai/skills) | MIT | `skills/greploop/LICENSE` |
| `greploop-apps` | derived from greptileai's greploop | MIT | `skills/greploop-apps/LICENSE` |
| `new-feature` | [michaelshimeles/skills](https://github.com/michaelshimeles/skills) | see "Unresolved" below | none upstream |
| `release-train` | this repository | MIT | root `LICENSE` |
| `skill-forge` | this repository | MIT | root `LICENSE` |
| `unslop` | [cursor/plugins (pstack)](https://github.com/cursor/plugins/tree/main/pstack/skills/unslop) | MIT | `skills/unslop/LICENSE` |
| `windows-shell` | this repository | MIT | root `LICENSE` |

## Unresolved: three skills with no upstream license

`code-structure`, `evidence-driven-testing`, and `new-feature` came from
[michaelshimeles/skills](https://github.com/michaelshimeles/skills). That
repository ships **no root LICENSE file**. Under the Berne Convention and US
copyright law, "no license" means all rights reserved, not public domain.
Publishing the repo publicly does not grant redistribution rights, and GitHub's
Terms of Service grant forking *on GitHub* only, not republication to npm under
a different name.

The frontmatter in those three files currently declares `license: MIT`. That
declaration is aspirational until one of the options below is done. **Resolve
this before the first npm publish.** Pick one:

1. **Ask.** Open an issue on michaelshimeles/skills requesting an explicit
   license, or email the author. Many people simply forgot the file and will
   add MIT on request. This is the cheapest fix and the one to try first.
2. **Rewrite.** Copyright covers expression, not ideas. The service-layer
   split, the worktree-per-task convention, and the record-evidence-while-you-test
   loop are all unprotectable ideas. Rewrite the three files from scratch in
   your own words and the problem disappears. Budget a few hours.
3. **Drop them.** Publish the seven skills whose provenance is clean and add
   the other three back later. Nothing else in the package depends on them.
4. **Do not publish them.** Keep them local-only by setting
   `metadata.internal: true` in their frontmatter, which hides them from
   discovery unless `INSTALL_INTERNAL_SKILLS=1` is set.

Options 2 and 3 are the ones that do not depend on someone else answering.

## PolyForm Shield and `before-and-after`

PolyForm Shield 1.0.0 permits use, copying, modification, and distribution.
Redistributing this skill inside the package is allowed, with two conditions
that bind you:

- **Clause 1**: you may not use the software to provide a product or service
  that competes with it. A skills bundle that drives the `@vercel/before-and-after`
  CLI is a consumer of it, not a competitor. Building a rival screenshot-diff
  product out of this skill would cross the line.
- **Clause 2**: you may not remove or obscure licensing or copyright notices.
  `skills/before-and-after/LICENSE` must stay in the published package. The
  `files` array in `package.json` ships the whole `skills/` tree, so it does,
  and `scripts/lint-skills.mjs` fails the build if any vendored skill loses its
  LICENSE file.

Note that PolyForm Shield is a source-available license, not an OSI-approved
open source one. The root `LICENSE` says MIT; that MIT grant does not extend to
this folder.

## Modifications made to vendored skills

Honest accounting of what differs from upstream.

### `unslop`

Body matches upstream. Frontmatter has three changes:

- Dropped `disable-model-invocation: true` so agents apply the skill on their
  own instead of waiting for a typed `/unslop`.
- Rewrote the description to name the trigger (text the agent writes or edits
  for a human reader) in place of upstream's "any writing. Must always apply.",
  so auto-invocation matches the scope `AGENTS.md` gives it.
- Added `license`, `compatibility`, and `metadata`.

Restore `disable-model-invocation: true` if you want slash-command-only behaviour.

### `greploop` and `greploop-apps`

Bodies match upstream. Added `metadata.vendored-from` and bumped
`metadata.version` to 1.4 to distinguish the packaged copy from upstream's 1.3.

### `before-and-after`

- Rewrote the Image Upload section. Upstream told the agent to call
  `./scripts/upload-and-copy.sh`, which resolves against the user's repository
  once the skill is installed under `.claude/skills/` and fails. The skill now
  resolves its own directory first.
- Added the Windows and Git Bash constraint to `compatibility`, and two rows to
  the error table.

### `new-feature`

- The port check assumed `lsof`, which Windows does not have. Added the
  `netstat` equivalent.

## Third-party tools these skills drive

The skills invoke tools they do not bundle. Installing and licensing those is
the user's responsibility.

`@vercel/before-and-after` and `agent-browser` (npm), Greptile (a hosted code
review service), the GitHub CLI `gh`, the GitLab CLI `glab`, Perforce `p4`,
FFmpeg (LGPL or GPL depending on build), Playwright, and `cua-driver` from
[trycua/cua](https://github.com/trycua/cua).
