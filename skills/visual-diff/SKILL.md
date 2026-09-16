---
name: visual-diff
description: Captures before/after screenshots of web pages or elements for visual comparison. Use when user says "take before and after", "screenshot comparison", "visual diff", "PR screenshots", "compare old and new", or needs to document UI changes. Accepts two URLs (file://, http://, https://) or two image paths.
license: PolyForm-Shield-1.0.0
compatibility: >
  Requires Node.js 18+ and the @vercel/before-and-after CLI (plus agent-browser).
  Chrome must be launchable; in containers and VMs set AGENT_BROWSER_ARGS="--no-sandbox".
  The bundled upload scripts are POSIX shell and need bash plus curl, so on Windows run
  them from Git Bash or WSL. PR integration requires an authenticated gh CLI.
metadata:
  author: vercel-labs
  vendored-from: https://github.com/vercel-labs/before-and-after
  version: "1.1"
allowed-tools:
  - Bash(npx @vercel/before-and-after *)
  - Bash(visual-diff *)
  - Bash(which before-and-after)
  - Bash(npm install -g @vercel/before-and-after)
  - Bash(*/upload-and-copy.sh *)
  - Bash(curl -s -o /dev/null -w *)
  - Bash(gh pr view *)
  - Bash(gh pr edit *)
  - Bash(vercel inspect *)
  - Bash(vercel whoami)
  - Bash(which vercel)
  - Bash(which gh)
---

# Visual diff

> **Package:** `@vercel/before-and-after`
> Never use `visual-diff` (wrong package).

## Agent Behavior Rules

**DO NOT:**
- Switch git branches, stash changes, start dev servers, or assume what "before" is
- Use `--full` unless user explicitly asks for full page / full scroll capture

**DO:**
- Use `--markdown` when user wants PR integration or markdown output
- Use `--mobile` / `--tablet` if user mentions phone, mobile, tablet, responsive, etc.
- Assume current state is **After**
- If user provides only one URL or says "PR screenshots" without URLs, **ASK**: "What URL should I use for the 'before' state? (production URL, preview deployment, or another local port)"

## Execution Order (MUST follow)

1. **Pre-flight** — `which before-and-after || npm install -g @vercel/before-and-after`
2. **Protection check** — if `.vercel.app` URL: `curl -s -o /dev/null -w "%{http_code}" "<url>"` (401/403 = protected)
3. **Capture** — `visual-diff "<before-url>" "<after-url>"`
4. **Upload** - `"$SKILL_DIR/scripts/upload-and-copy.sh" <before.png> <after.png> --markdown`
5. **PR integration** — optionally `gh pr edit` to append markdown

**Never skip steps 1-2.**

## Quick Reference

```bash
# Basic usage
visual-diff <before-url> <after-url>

# With selector
visual-diff url1 url2 ".hero-section"

# Different selectors for each
visual-diff url1 url2 ".old-card" ".new-card"

# Viewports
visual-diff url1 url2 --mobile    # 375x812
visual-diff url1 url2 --tablet    # 768x1024
visual-diff url1 url2 --full      # full scroll

# From existing images
visual-diff before.png after.png --markdown

# Via npx (use full package name!)
npx @vercel/before-and-after url1 url2
```

| Flag | Description |
|------|-------------|
| `-m, --mobile` | Mobile viewport (375x812) |
| `-t, --tablet` | Tablet viewport (768x1024) |
| `--size <WxH>` | Custom viewport |
| `-f, --full` | Full scrollable page |
| `-s, --selector` | CSS selector to capture |
| `-o, --output` | Output directory (default: ~/Downloads) |
| `--markdown` | Upload images & output markdown table |
| `--upload-url <url>` | Custom upload endpoint (default: 0x0.st) |

## Image Upload

`$SKILL_DIR` is the folder this `SKILL.md` lives in. Resolve it before calling
the scripts - once the skill is installed under `.claude/skills/`, `~/.claude/skills/`,
or `.agents/skills/`, a bare `./scripts/...` resolves against the repo you are
working in and fails.

```bash
SKILL_DIR="$(dirname "$(find . ~/.claude/skills .claude/skills .agents/skills   -name SKILL.md -path '*visual-diff*' 2>/dev/null | head -1)")"

# Default (0x0.st - no signup needed)
"$SKILL_DIR/scripts/upload-and-copy.sh" before.png after.png --markdown

# GitHub Gist
IMAGE_ADAPTER=gist "$SKILL_DIR/scripts/upload-and-copy.sh" before.png after.png --markdown
```

The scripts are POSIX shell. On Windows, run them from Git Bash or WSL; PowerShell
cannot execute them directly.

## Vercel Deployment Protection

If `.vercel.app` URL returns 401/403:

1. Check Vercel CLI: `which vercel && vercel whoami`
2. If available: `vercel inspect <url>` to get bypass token
3. If not: Tell user to provide bypass token, take manual screenshots, or disable protection

## PR Integration

```bash
# Check for gh CLI
which gh

# Get current PR
gh pr view --json number,body

# Append screenshots to PR body
gh pr edit <number> --body "<existing-body>

## Before and After
<generated-markdown>"
```

If no `gh` CLI: output markdown and tell user to paste manually.

## Error Reference

| Error | Fix |
|-------|-----|
| `command not found` | `npm install -g @vercel/before-and-after` |
| `could not determine executable` | Use `npx @vercel/before-and-after` (full name) |
| 401/403 on .vercel.app | See Vercel protection section |
| Element not found | Verify selector exists on page |
| `upload-and-copy.sh: No such file` | Resolve `$SKILL_DIR` first (see Image Upload) - the path is relative to the skill, not your repo |
| `No usable sandbox` (container/VM) | `export AGENT_BROWSER_ARGS="--no-sandbox"` before capturing |
