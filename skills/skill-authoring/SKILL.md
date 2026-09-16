---
name: skill-authoring
description: Write, audit, and fix agent skills so they load in every harness and survive review. Use when creating a new SKILL.md, when a skill is not being invoked or not discovered, when frontmatter fails validation, or before publishing a skills repo to npm or skills.sh.
license: MIT
compatibility: Requires Node.js 18+ for the validator (npx skills add --list). Everything else is plain file editing and works in any harness.
metadata:
  author: software-factory
  version: "1.0"
allowed-tools: Bash(npx skills *) Bash(node:*) Bash(git:*)
---

# Skill authoring

A skill is a folder with a `SKILL.md` inside it. The folder name and the
frontmatter `name` must match, and the description decides whether the agent
ever loads the file. Most broken skills are broken in the description, not the
body.

## The description is the whole product

The agent sees only `name` and `description` until it decides to load the
skill. The body might be perfect and never get read. Write the description as a
trigger list, not a summary.

Bad, because it describes the skill instead of the moment it applies:

```yaml
description: A comprehensive guide to service layer architecture patterns.
```

Good, because every clause is a situation the agent can recognise:

```yaml
description: >
  Use when multiple workflows duplicate the same operational logic, when
  deciding what belongs in actions vs shared services, or when refactoring
  repeated operational blocks across domain flows.
```

Rules that hold up:

- Lead with "Use when". The agent is pattern-matching on situations.
- Name the words a person would actually type. If someone says "screenshot
  comparison", that phrase belongs in the description verbatim.
- Say what it does not cover, when the boundary is easy to get wrong.
- Keep it under roughly 500 characters. Longer descriptions get skimmed.

## Frontmatter fields

Required by the spec:

| Field | Notes |
|---|---|
| `name` | Lowercase, hyphens. Must equal the folder name. |
| `description` | Trigger-focused. See above. |

Worth adding on anything you publish:

| Field | Notes |
|---|---|
| `license` | SPDX id. Vendored skills keep the upstream id, not yours. |
| `compatibility` | Every binary, runtime, credential, and OS assumption. |
| `metadata.author` | Who owns it. |
| `metadata.version` | Bump on every behaviour change. |
| `allowed-tools` | Narrow the blast radius. Claude Code and most others honour it. |
| `metadata.internal` | `true` hides it from discovery until `INSTALL_INTERNAL_SKILLS=1`. |

`compatibility` is the field people skip and then field bug reports about.
Write it as if the reader has none of your tools installed, because they do not.

## Layout that gets discovered

The `skills` CLI walks a fixed set of container directories up to three levels
deep. Put skills in `skills/<name>/SKILL.md` at the repo root. That layout is
discovered by every agent the CLI supports.

```
repo/
  skills/
    my-skill/
      SKILL.md          # required
      LICENSE           # if vendored, or if the skill differs from the repo
      scripts/          # executables the skill calls
      references/       # long material the body links to
```

If no skills turn up in a known container the CLI falls back to a recursive
search. Do not rely on that. It is slower and the fallback has bitten people
who nested skills under `examples/` or `docs/`.

## Keep the body short and link out

The body is loaded into context every time the skill fires. Long bodies cost
tokens on every invocation and get skimmed. Anything over roughly 200 lines
should move to `references/` and be linked from the body.

Good candidates for `references/`: API schemas, GraphQL queries, error-code
tables, long worked examples, provider-specific setup.

Keep in the body: the decision rules, the execution order, the traps.

## Paths inside a skill

Never write `./scripts/foo.sh` in a skill body. Once installed, the skill lives
in `.claude/skills/<name>/` or `~/.claude/skills/<name>/` or one of fifty other
agent directories, and `./` resolves against the user's repo instead. Tell the
agent to resolve the skill directory first, then call scripts through it.

Scripts must be executable and should work on Linux, macOS, and Windows, or say
plainly in `compatibility` which ones they do not support.

## Audit checklist

Run this before publishing a skill or when one is not firing.

1. `npx skills add . --list` from the repo root. Every skill you expect should
   be listed. A missing skill means bad frontmatter or a layout the walker
   never reaches.
2. Folder name equals frontmatter `name`.
3. Frontmatter is valid YAML. A colon inside an unquoted description is the
   usual culprit. Use `>` block scalars for anything with punctuation.
4. Description leads with a trigger and names real user phrasing.
5. `compatibility` lists every external binary, credential, and OS constraint.
6. No bare relative paths to scripts in the body.
7. Body is under ~200 lines, or the overflow lives in `references/`.
8. Scripts referenced from the body actually exist and are executable.
9. `allowed-tools` covers everything the body tells the agent to run, and
   nothing more.
10. Vendored skills keep their upstream `LICENSE` file and name the source in
    `metadata.vendored-from`.

## When a skill does not fire

Work down this list in order.

- Confirm it is installed where the agent looks: `npx skills list`.
- Confirm the frontmatter parses. Broken YAML makes the skill invisible with no
  error shown to the user.
- Reread the description as the agent sees it. If it describes capability
  instead of a triggering situation, rewrite it and try again.
- Check for a second skill with an overlapping description. Two skills
  competing for the same trigger means neither fires reliably. Narrow one.
- As a last resort, invoke it explicitly by name to confirm the body works.
  That separates a discovery problem from a content problem.
