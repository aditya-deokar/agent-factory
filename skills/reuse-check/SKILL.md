---
name: reuse-check
description: >
  Check whether code you are about to create already exists in this repository. Use before creating
  any new class, service, repository, hook, component, module, helper or dependency; when a name you
  are about to write ends in Service, Manager, Helper, Util, Repository, Store, Client or Validator;
  and when a plan lists "New Abstractions". Skip for edits inside existing files that add no new symbol.
license: MIT
compatibility: >
  Needs the agent-factory MCP server or the agent-factory CLI on PATH, and a completed
  agent-factory audit for this repository.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) mcp__agent-factory__find_reusable mcp__agent-factory__impact_of
---

# Reuse check

Duplicate abstractions are how codebases drift: a second token service, a second mailer, a
second repository for the same table. Before you create something new, prove it does not exist.

## Steps

1. For each new abstraction in your plan, call `find_reusable` with its name, a one-line
   description, and the methods you intend it to have.
   CLI fallback:

   ```bash
   agent-factory reuse InvitationTokenService --desc "create, validate and expire invitation tokens" --methods create,validate,expire --json
   ```

2. Read the verdict of the top candidate:
   - `reuse`: use the existing symbol. If you need to change its signature, call `impact_of` on it
     first and include every dependent in your plan.
   - `extend`: extend the existing symbol (a parameter, a purpose, a strategy, a method) instead of
     writing a sibling next to it.
   - `new_ok`: nothing fits. Proceed, and write one sentence in the plan saying why nothing existing fits.
3. Record the decision for every proposal in the plan's reuse decisions (`record_plan`, or
   `agent-factory feature plan`).
4. Never overrule a `reuse` verdict silently. If you disagree, say why in the plan and to the
   developer. The developer decides.

## What the evidence means

The report explains its score: `vector` (meaning), `name` (naming), `methods` (does it already do
what you proposed), `maturity` (how many places already reuse it) and `pattern` (does it follow a
validated pattern). A high `maturity` symbol is a shared building block: extending it is usually
right, forking it is usually wrong.
