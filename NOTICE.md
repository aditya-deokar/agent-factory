# Notice and attribution

`@software-factory/skills` contains original work and vendored work. Vendored
skills keep their upstream licenses, included in their own folders. The MIT
license at the repo root covers everything else.

Update this file whenever a skill is added or its provenance changes.

## Origin of every skill

| Skill | Origin | License | License file |
|---|---|---|---|
| `before-and-after` | [vercel-labs/before-and-after](https://github.com/vercel-labs/before-and-after) | PolyForm Shield 1.0.0 | `skills/before-and-after/LICENSE` |
| `code-structure` | written for this repository | MIT | root `LICENSE` |
| `evidence-driven-testing` | written for this repository | MIT | root `LICENSE` |
| `greploop` | [greptileai/skills](https://github.com/greptileai/skills) | MIT | `skills/greploop/LICENSE` |
| `greploop-apps` | derived from greptileai's greploop | MIT | `skills/greploop-apps/LICENSE` |
| `new-feature` | written for this repository | MIT | root `LICENSE` |
| `release-train` | written for this repository | MIT | root `LICENSE` |
| `skill-forge` | written for this repository | MIT | root `LICENSE` |
| `unslop` | [cursor/plugins (pstack)](https://github.com/cursor/plugins/tree/main/pstack/skills/unslop) | MIT | `skills/unslop/LICENSE` |
| `windows-shell` | written for this repository | MIT | root `LICENSE` |

## On the three rewritten skills

`code-structure`, `evidence-driven-testing`, and `new-feature` began as copies
from [michaelshimeles/skills](https://github.com/michaelshimeles/skills), a
repository that ships no license file. No license means all rights reserved, so
redistributing those copies was not something this package could do.

All three were rewritten from scratch before the first publish. The current
files share no text with the originals. What they share is subject matter, and
subject matter is not protected: separating orchestration from mechanism, giving
each task its own worktree, and capturing proof while testing are ideas in
common circulation, not expression owned by anyone.

The bundled recorder was replaced as part of this. The original
`scripts/evidence.py` was removed along with its test suite, and
`scripts/record.py` is an independent implementation with its own architecture,
its own session format, and its own tests in `tests/test_record.py`.

Nothing from that upstream repository remains in this package.

## PolyForm Shield and `before-and-after`

PolyForm Shield 1.0.0 permits use, copying, modification, and distribution.
Shipping this skill inside the package is allowed, with two conditions that
bind you:

- **Clause 1**: you may not use the software to provide a product or service
  that competes with it. A skills bundle that drives the
  `@vercel/before-and-after` CLI consumes it rather than competing with it.
  Building a rival screenshot-diff product out of this skill would cross the
  line.
- **Clause 2**: you may not remove or obscure licensing or copyright notices.
  `skills/before-and-after/LICENSE` must stay in the published package. The
  `files` array in `package.json` ships the whole `skills/` tree, and CI fails
  the build if any vendored skill loses its LICENSE or if that LICENSE is
  missing from the npm tarball.

PolyForm Shield is source-available, not OSI-approved open source. The root MIT
grant does not extend to this folder.

## Modifications to vendored skills

### `unslop`

Body matches upstream. Frontmatter differs in three ways:

- Dropped `disable-model-invocation: true` so agents apply the skill on their
  own instead of waiting for a typed `/unslop`.
- Rewrote the description to name the trigger (text the agent writes or edits
  for a human reader) in place of upstream's "any writing. Must always apply.",
  so auto-invocation matches the scope `AGENTS.md` gives it.
- Added `license`, `compatibility`, and `metadata`.

Restore `disable-model-invocation: true` for slash-command-only behaviour.

### `greploop` and `greploop-apps`

Bodies match upstream. Added `metadata.vendored-from` and bumped
`metadata.version` to 1.4 to distinguish the packaged copy from upstream's 1.3.

### `before-and-after`

- Rewrote the Image Upload section. Upstream told the agent to call
  `./scripts/upload-and-copy.sh`, which resolves against the user's repository
  once the skill is installed under `.claude/skills/` and fails there. The skill
  now resolves its own directory first.
- Added the Windows and Git Bash constraint to `compatibility`, and two rows to
  the error table.

## Third-party tools these skills drive

The skills invoke tools they do not bundle. Installing and licensing those is
the user's responsibility.

`@vercel/before-and-after` and `agent-browser` (npm), Greptile (a hosted code
review service), the GitHub CLI `gh`, the GitLab CLI `glab`, Perforce `p4`,
FFmpeg (LGPL or GPL depending on build), and Playwright.
