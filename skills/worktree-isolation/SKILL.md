---
name: worktree-isolation
description: >
  Set up an isolated branch and working directory before touching any code, so
  concurrent agents and sessions cannot corrupt each other's work. Use before
  the first edit of any feature, fix, or chore, and use it whenever more than
  one agent or session shares a repository. Covers the overlap check against
  work already in flight, naming, dependency setup, and teardown after merge.
license: MIT
compatibility: >
  Requires git 2.5 or newer for worktree support. The overlap check uses the
  GitHub CLI (gh) when available and falls back to reading branches. Claude Code
  and Cursor provision worktrees themselves; read the harness section first.
  Port inspection differs per platform and both forms are given.
metadata:
  author: software-factory
  version: "2.0"
allowed-tools: Bash(git:*) Bash(gh pr list*) Bash(gh pr diff*) Bash(lsof:*) Bash(netstat:*)
---

# Worktree isolation

Before the first edit, you need two things: a branch nobody else is on, and a
directory nobody else is writing to. A branch alone is not enough. Two agents
sharing one checkout will interleave edits into each other's files no matter
which branches are checked out, because there is only one working tree.

Git worktrees give you both. One command, and you have a second directory on
its own branch, sharing the object database with the original.

## Read this first: your harness may have done it already

**Claude Code** provisions a worktree per session under `.claude/worktrees/`
and checks out a branch for you. You are already isolated. Skip the setup
below, keep the branch name you were given, and use only the overlap check and
the teardown notes.

**Cursor** does the same for branches named `worktree-*`.

**Anything else**, including a plain terminal: do the whole thing.

Check before assuming:

```bash
git rev-parse --show-toplevel   # a path under .claude/ or .worktrees/ means you are already in one
git branch --show-current       # anything other than main/master means a branch exists for you
```

## Check for overlap before you start

This is the step that gets skipped and the step that pays. Two agents editing
the same file on separate branches produce a conflict that surfaces at merge,
after both have finished, when unwinding either one is expensive.

Finding out first costs thirty seconds.

```bash
git fetch origin --prune

# What is already in flight, and which files it touches
gh pr list --state open
gh pr diff <number> --name-only

# Without gh: recent branches and their diff against main
git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/remotes/origin | head
git diff --name-only origin/main...origin/<branch>
```

Also look for uncommitted work in the checkout you are standing in. Another
agent or a human may be mid-task:

```bash
git status --porcelain
```

**If your task needs a file that open work already touches, stop and ask.** Do
not start and hope. The question to raise is whether to wait for the other
branch to land, coordinate a split, or take a different approach. That is a
decision for whoever is directing the work, not one to make silently.

## Set up

```bash
git fetch origin
git worktree add ../wt/<task-name> -b agent/<task-name> origin/main
cd ../wt/<task-name>
git branch --show-current     # confirm: agent/<task-name>, not main
```

Three details matter.

**Branch from `origin/main`, not from local `main`.** Local `main` is stale as
often as not, and starting there means your first rebase is also your first
merge conflict. Substitute `master` if that is the repo's default.

**Name it for the task, with something unique on the end.** `fix-login-4f2a`,
not `fix` and not `patch-1`. The suffix exists because `git worktree add`
refuses a name that already exists, and when it refuses you should pick a new
name rather than delete whatever is there. Something else is using it.

**Put worktrees outside the repo, or inside a gitignored directory.** A
worktree created at `./feature-x` inside the repo shows up as untracked files
and eventually somebody commits it. `../wt/` sidesteps this entirely. If the
repo already has a convention, follow that instead.

Then install dependencies inside the new directory:

```bash
npm ci        # or pnpm install --frozen-lockfile, uv sync, bundle install
```

A worktree does not share `node_modules`, `.venv`, or any other install
directory with its parent. A fresh worktree with no install will fail in ways
that look like code bugs and are not.

## What isolation does not cover

The worktree isolates files. It isolates nothing else, and each of these has
bitten people who assumed otherwise.

**Ports.** Your dev server and the other agent's both want 3000. The one that
starts second either fails or silently attaches to the first. Before trusting
what a port serves, confirm it is yours:

```bash
lsof -i :3000                      # Linux, macOS
netstat -ano | findstr :3000       # Windows
```

Better: set an explicit port per worktree and never rely on the default.

**Databases.** A shared dev database means your migration is everyone's
migration. Run schema changes against a database you created for this task, or
coordinate out loud before touching a shared one.

**Lockfiles.** Two branches adding dependencies produce a lockfile conflict.
Never hand-merge one. The file is generated, and a hand-merged lockfile can
install a dependency tree that no resolver would ever produce. Take one side,
rerun the install, commit the result:

```bash
git checkout --theirs package-lock.json && npm install
```

**Global caches and config.** `~/.npmrc`, `~/.aws/config`, Docker containers,
and anything else outside the repo is shared. Changing them affects every
agent on the machine.

## Tear down after the PR lands

Worktrees are cheap but they are not free: each one is a full checkout plus its
own `node_modules`. Leaving them around fills the disk and, worse, leaves stale
directories that someone eventually edits by mistake.

```bash
cd <main-repo>
git worktree remove ../wt/<task-name>
git branch -D agent/<task-name>
git fetch origin --prune
```

`-D` rather than `-d` is correct here. After a squash merge or rebase merge the
commits on your branch do not exist upstream under those hashes, so git does
not believe the branch is merged and `-d` refuses. The work is in `main`; the
branch object is just bookkeeping.

If `git worktree remove` complains about uncommitted changes, look at them
before forcing. That is either work you forgot to commit or a build artifact.
Only one of those is safe to discard.

**Keep the worktree until the PR is merged or closed.** Review comments arrive
after you think you are finished, and recreating a deleted worktree to make a
two-line change is annoying enough that people push fixes from the wrong
branch instead.

## Rules that hold regardless of harness

- Never commit to `main` or `master` directly.
- One branch and one worktree per task. Never edit inside someone else's.
- Never `git push --force`. Use `--force-with-lease`, only on your own branch.
  The difference is that `--force-with-lease` refuses when someone else has
  pushed since you last fetched, which is exactly the case where force-pushing
  destroys work.
- Rebase onto the latest `origin/main` before opening the PR, then rerun the
  checks. A branch that passed against a week-old base has proved nothing about
  the current one.
- If a conflict is not obviously resolvable, stop and report it. A confidently
  wrong merge resolution is harder to find later than an unmerged branch.
