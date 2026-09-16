---
name: service-layer
description: >
  Decide whether code belongs in an orchestration boundary or a shared service,
  and extract shared mechanics safely. Use when the same operational logic
  appears in more than one workflow, when a bug fixed in one flow is still live
  in another doing the same thing, when a route handler has grown past its own
  domain rules, or when adding a feature whose plumbing already exists elsewhere.
license: MIT
compatibility: >
  Language-agnostic. Examples are TypeScript, but the split applies to any
  codebase with an entry point that receives requests and a body of logic behind
  it, including HTTP handlers, server actions, CLI commands, queue consumers,
  and cron jobs.
metadata:
  author: software-factory
  version: "2.0"
---

# Service layer

Two questions decide where a piece of code lives.

**Would this change if the product rules changed?** If yes, it belongs at the
boundary. Who is allowed to do this, when it is allowed, what happens on
failure, which state transition follows: all of that is product.

**Would this change if the vendor changed?** If yes, it belongs in a service.
Retry counts, SDK calls, connection handling, payload shapes, polling until
ready: all of that is mechanism.

Code that answers yes to both is doing two jobs and should be split.

## Why it matters

The symptom that brings people here is a bug fixed in one place and still live
in two others. Three routes each build their own Stripe customer payload. One
of them gets a fix for a missing idempotency key. The other two do not, because
nothing connected them.

Centralising the mechanism means the next fix lands everywhere at once. That is
the entire payoff. Everything below is in service of it.

## The shape

```
Boundary                          Service
─────────────────────────────     ─────────────────────────────
authenticates the caller          talks to the vendor or driver
checks permission                 retries and backs off
decides whether to act            polls until ready
picks the arguments               validates its own inputs
interprets the result             returns a structured result
maps failure to a response        raises typed errors
writes the state transition       never touches app state
```

The line is one-way. A boundary calls a service. A service never calls a
boundary, never reads the session, never writes the domain tables, and never
decides whether an action was allowed.

When a service starts needing to know who the user is, the design has drifted.
Pass what it needs as an argument instead.

## What a service function looks like

Small, composable, and honest about failure.

```ts
// services/storage.ts

type UploadResult =
  | { ok: true; key: string; bytes: number; contentType: string }
  | { ok: false; reason: "too-large" | "bad-type" | "upstream"; detail: string };

export async function putObject(input: {
  bucket: string;
  key: string;
  body: Uint8Array;
  contentType: string;
  maxBytes: number;
}): Promise<UploadResult> {
  if (input.body.byteLength > input.maxBytes) {
    return { ok: false, reason: "too-large", detail: `${input.body.byteLength} bytes` };
  }
  // ... SDK call, retry, checksum
}
```

Four properties are doing the work:

**Everything arrives as a parameter.** No reading config, session, or database
inside. The caller supplies `maxBytes` because the limit is a product decision
and different callers want different limits. A service that hardcodes it has
stolen a decision from its callers.

**The return is structured.** `ok: true` carries what the caller needs next.
`ok: false` carries a reason the caller can branch on. A boolean tells the
caller nothing; a thrown string forces string matching.

**Failure is in the type.** The caller cannot forget to handle it, because the
compiler will not let them read `.key` without narrowing first.

**It does one thing.** `putObject` does not also record an audit row or send a
notification. Those are separate calls the boundary makes in whatever order its
rules require.

Then the boundary stays readable, because it reads like the rules:

```ts
// app/api/avatar/route.ts
export async function POST(req: Request) {
  const user = await requireUser(req);                      // product: who
  if (!user.canUploadAvatar) return forbidden();            // product: whether

  const file = await readFile(req);
  const result = await putObject({                          // mechanism
    bucket: AVATAR_BUCKET,
    key: `avatars/${user.id}`,
    body: file.bytes,
    contentType: file.type,
    maxBytes: user.plan === "pro" ? 10_000_000 : 2_000_000, // product: limits
  });

  if (!result.ok) {
    return result.reason === "too-large"
      ? badRequest("Avatar must be under your plan limit")  // product: wording
      : serverError();
  }

  await db.user.update({ where: { id: user.id }, data: { avatarKey: result.key } });
  return ok({ url: publicUrl(result.key) });
}
```

Every line in the handler is a decision someone could argue about in a product
meeting. Nothing in it is about S3.

## Extracting from existing code

Do not refactor everything at once. The steps are ordered so that you can stop
after any of them and still have working code.

1. **Find the real duplication.** Two functions that look similar are not
   necessarily doing the same thing. Read both and ask whether a change to one
   should always change the other. If the answer is no, they are coincidental
   twins and merging them creates a function with a flag argument, which is
   worse than the duplication.

2. **Extract for one caller only.** Pull the mechanism out, keep the signature
   shaped by that single caller's needs, and leave the other callers untouched.
   The code now exists in two places, which feels wrong and is temporary.

3. **Verify that caller.** Tests, typecheck, and the flow exercised for real.
   This is the checkpoint. If it is broken, exactly one caller is broken and
   you know which change did it.

4. **Migrate the next caller.** It will want something slightly different.
   That difference becomes a parameter, not a branch inside the service. If you
   find yourself adding `if (mode === "admin")`, stop: the difference is a
   product rule and belongs in the boundary.

5. **Delete the originals** once every caller is migrated. Skipping this leaves
   dead code that the next person will read and believe.

6. **Leave the domain logic where it was.** Auth checks, status transitions,
   and user-facing error text should not have moved. If they did, put them
   back.

## When not to do this

**One caller.** Extraction pays for itself at two. At one it buys indirection
and nothing else. Write it inline and extract when the second caller shows up.

**Throwaway code.** Structure is a bet that this code will be changed many
times. A migration script run once does not collect on that bet.

**The duplication is shrinking.** If two flows are converging and one will be
deleted next month, wait for the deletion.

## Ways this goes wrong

**The god service.** One `handleUpload()` that authenticates, validates,
stores, records, and notifies. It has one caller by construction, because
nothing else wants that exact sequence. The signal is a function whose name
contains "handle", "process", or "do".

**The leaky service.** The service writes to the database directly, so calling
it has consequences the caller cannot see. Now nobody can call it from a
transaction, a test, or a dry run. Services return values; callers write state.

**The flag parameter.** `sendEmail(to, body, { skipValidation, asAdmin, silent })`.
Each flag is a product rule that leaked in. Three booleans is eight behaviours
and your tests cover two of them.

**Mismatched siblings.** One service function throws, the next returns null,
the third returns `{ error }`. Callers cannot develop a habit, so every call
site handles failure differently and some do not handle it at all. Pick one
convention per module and hold it.

**Anticipatory abstraction.** A service layer built before the second caller
exists, shaped around imagined future needs. It will be wrong, and it will be
load-bearing by the time you find out.

## Quick test

Read a function and ask what would make it change.

| What would change it | Where it goes |
|---|---|
| A pricing decision | boundary |
| A permission rule | boundary |
| The wording of an error a user sees | boundary |
| A vendor swap | service |
| A timeout or retry policy | service |
| A new required field in an API payload | service |
| A change to which users get the feature | boundary |
| A change to how the feature physically works | service |

If a function would change for reasons on both sides of this table, it is two
functions.
