---
name: test-evidence
description: >
  Produce a checkable artifact that a change works, instead of asserting it in
  prose. Use whenever a claim about behavior needs backing: before opening a PR,
  when reporting a fix, when asked whether something works, or when a reviewer
  has to take your word for it. Covers recorded UI sessions, headless capture
  without a display, and measured evidence for changes with no visible surface.
license: MIT
compatibility: >
  The recorder (scripts/record.py) needs Python 3.9+ and ffmpeg built with
  libx264 and libass. Capture source by platform: X11 via DISPLAY on Linux,
  avfoundation with Screen Recording permission on macOS, gdigrab on Windows.
  wlroots Wayland needs wf-recorder instead; GNOME and KDE Wayland are not
  supported. Run 'record.py doctor' to check. The headless path needs only a
  running app and a scriptable browser. Attaching evidence to a PR needs gh.
metadata:
  author: software-factory
  version: "2.0"
allowed-tools: Bash(python*) Bash(ffmpeg*) Bash(ffprobe*) Bash(gh pr*) Bash(gh issue*) Bash(npx playwright*)
---

# Test evidence

"I tested it and it works" is not a result. It is a request to be trusted, and
it puts the verification back on the reader, who now has to read the diff you
were supposed to have verified.

Replace the sentence with an artifact. The artifact is whatever a skeptical
reviewer would need in order to stop asking.

## What counts

| Kind of change | Evidence |
|---|---|
| Visible UI behavior | A recording of you driving it, annotated |
| Visual change with no interaction | Before and after screenshots of the same viewport |
| Bug fix | The failure reproduced first, then the same steps passing |
| Performance | The same measurement before and after, same machine, same input |
| API or CLI change | The request and response, or the command and its output, both versions |
| Data or migration | Row counts and a sample, queried before and after |
| Refactor with no behavior change | The test suite passing, plus the one behavior most at risk |

Two rules apply to every row.

**Capture the before state first.** Before the fix, while the bug still
reproduces. That state is free to record right now and expensive to reconstruct
later, once your fix is in the working tree and showing the old behavior means
stashing everything.

**Vary one thing.** Before and after must differ only by your change. A
screenshot at a different window width, or a benchmark on a loaded machine,
proves nothing and will be dismissed by anyone who notices.

## The recording path

Use it when the change has a surface a person interacts with. The recording
shows you performing the test, so what the reviewer watches is the test itself,
not a demo of the happy path.

```bash
REC="skills/test-evidence/scripts/record.py"

python "$REC" doctor                         # once per machine
python "$REC" start --label "Cart totals with tax"
```

Then drive the app yourself, clicking and typing through each case, and
annotate as you go. Annotations are timestamped against the recording and burned
into the video at the moment they happened.

```bash
python "$REC" annotate "Added 2 items to cart"
python "$REC" annotate "Subtotal shows 49.98" --kind pass
python "$REC" annotate "NY tax shows 0.00, expected 4.44" --kind fail
```

Kinds are `step`, `pass`, `fail`, and `note`. They colour the burned-in caption
and group the report, so use `pass` and `fail` for assertions rather than
narrating everything as a `step`.

```bash
python "$REC" stop
```

You get `evidence.mp4` with captions burned in, `report.md` with a timeline and
a failures section, and `manifest.json` for anything programmatic. Default
output is `.artifacts/evidence/session/`; `--dir` puts it elsewhere.

### Recording well

- **Annotate the assertion, not the click.** "Clicked submit" is visible in the
  video. "Order confirmation shows the discounted total" is the claim.
- **Record the failure too.** A `fail` annotation mid-session is more credible
  than a clean run, because it shows you were looking for problems rather than
  filming a rehearsed path.
- **Keep it short.** Sixty to ninety seconds. Long recordings do not get
  watched, and a reviewer who skips your evidence is back to reading the diff.
- **Slow down slightly.** Real-time clicking is hard to follow on replay.
- **Do not narrate the code.** The video is for behavior. The diff covers the
  implementation.

### If the recorder will not start

Run `doctor` first; it names the missing piece rather than making you guess.

| Symptom | Cause |
|---|---|
| `libx264` missing | ffmpeg built without it. Different build, not a flag. |
| `ass filter` missing | ffmpeg built without libass. Captions cannot burn in. |
| `no DISPLAY` on Linux | Headless. Use the headless path below. |
| Black video on macOS | Screen Recording permission not granted to the terminal. |
| `wayland` reported | Use `wf-recorder` for wlroots. GNOME and KDE are not supported. |

Raw capture is MPEG-TS, so a crashed or killed recorder still leaves playable
footage. If `stop` fails, `raw.ts` in the session directory is your recording.

## The headless path

No display, no recorder. Scripted capture instead, which is reproducible and in
some ways better evidence because anyone can rerun it.

```bash
npx playwright screenshot --viewport-size=1280,800 \
  http://localhost:3000/cart before.png
# apply the change, restart, then
npx playwright screenshot --viewport-size=1280,800 \
  http://localhost:3000/cart after.png
```

Pin the viewport explicitly. A default that differs between runs produces two
images that differ for reasons unrelated to your change, and the comparison is
worthless.

For flows rather than single pages, write a short script that walks the steps
and shoots at each one. Commit it. A committed capture script is evidence that
regenerates itself on the next change.

Hand the pair to `visual-diff` for a PR-ready table.

## Changes with no visible surface

These still need evidence. The form changes, not the requirement.

**Performance.** Same machine, same input, same warm state, several runs.

```bash
hyperfine --warmup 3 'node dist/parse.js fixtures/large.json'
```

Report both numbers and the spread. A single run of each is noise.

**Output.** Capture both and diff them.

```bash
git stash && ./cli report --format=json > /tmp/before.json
git stash pop && ./cli report --format=json > /tmp/after.json
diff -u /tmp/before.json /tmp/after.json
```

**Queries and data.** Run the count before and after the migration and paste
both, with the query. A number with no query beside it cannot be checked.

**Logs and traces.** The excerpt that shows the retry firing, the cache hit, the
error no longer thrown. Trim it to the lines that matter.

## Attaching it

Evidence in your terminal helps nobody. It goes where the decision gets made.

```bash
gh pr comment <number> --body-file .artifacts/evidence/session/report.md
```

Video cannot be uploaded through the `gh` CLI. Drag `evidence.mp4` into the PR
comment box in the browser, which uploads it to GitHub's CDN, and paste the
resulting URL. Images can go through `visual-diff --markdown`, which
uploads and prints the table.

Structure the PR body so a reviewer sees the proof before the explanation:

```markdown
## What changed
One or two sentences.

## Evidence
<video or before/after table>

| Case | Result |
|---|---|
| Cart with 2 items | pass |
| NY tax applied | pass |
| Empty cart | pass |

## Risks
What could still be wrong, and what is untested.
```

The risks section matters. Evidence shows what you checked; it says nothing
about what you did not. Naming the gap is the difference between evidence and
a sales pitch.

## Do not do these

**Do not record after the fact.** Rehearsing a path you already know works is a
demo, not a test. Record while you are genuinely checking, when you do not yet
know the answer.

**Do not crop out the failure.** If something broke mid-session, annotate it
and leave it in. Trimmed evidence is worse than none, because it destroys trust
in the evidence that is real.

**Do not claim more than you captured.** Three cases recorded means three cases
proved. "Fully tested" written under a video of three cases is a claim the
video contradicts.

**Do not skip it for small changes and say so.** If a change genuinely does not
warrant evidence, write one line saying what you did check. That is honest. An
unsupported "tested and working" is not.
