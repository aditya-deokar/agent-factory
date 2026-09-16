#!/usr/bin/env node
/**
 * Validates every skill in skills/ against the Agent Skills spec plus the
 * house rules in skills/skill-forge/SKILL.md.
 *
 * Exits non-zero on any error, so it is safe to wire into prepublishOnly and CI.
 * Usage: node scripts/lint-skills.mjs [--strict]
 */
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SKILLS_DIR = join(ROOT, "skills");
const STRICT = process.argv.includes("--strict");

const MAX_DESC = 1024;
const MAX_BODY_LINES = 260;

const errors = [];
const warnings = [];

const err = (skill, msg) => errors.push(`${skill}: ${msg}`);
const warn = (skill, msg) => warnings.push(`${skill}: ${msg}`);

/** Minimal frontmatter reader. Avoids a YAML dependency; we only need scalars. */
function parseFrontmatter(raw, skill) {
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!m) {
    err(skill, "no YAML frontmatter block (must open and close with ---)");
    return { fields: {}, body: raw };
  }
  const block = m[1];

  // A plain scalar containing ": " is ambiguous YAML. Real parsers reject it,
  // and the skill then vanishes from discovery with no error shown anywhere.
  // Block scalars (>) and quoted values are fine.
  for (const [i, line] of block.split(/\r?\n/).entries()) {
    const kv = line.match(/^ *([A-Za-z0-9_-]+):[ \t]+(.+)$/);
    if (!kv) continue;
    const value = kv[2].trim();
    if (/^[>|]/.test(value)) continue;                      // block scalar
    if (/^["'].*["']$/.test(value)) continue;               // fully quoted
    if (/^[[{]/.test(value)) continue;                      // flow collection
    if (/: /.test(value))
      err(skill, `frontmatter line ${i + 1} ("${kv[1]}") has an unquoted ": " - use a > block scalar or quote it`);
  }
  const body = raw.slice(m[0].length);
  const fields = {};
  const lines = block.split(/\r?\n/);

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim() || line.trimStart().startsWith("#")) continue;

    // Nested key (two-space indent), e.g. metadata.version
    const nested = line.match(/^ {2}([A-Za-z0-9_-]+):\s*(.*)$/);
    if (nested && fields.__parent) {
      fields[`${fields.__parent}.${nested[1]}`] = nested[2].trim();
      continue;
    }

    const top = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!top) continue;
    const [, key, rest] = top;

    if (rest === "" || rest === ">" || rest === "|" || rest === ">-" || rest === "|-") {
      // Block scalar or a parent with children. Collect the indented run.
      const collected = [];
      let j = i + 1;
      while (j < lines.length && (lines[j].startsWith("  ") || lines[j].trim() === "")) {
        collected.push(lines[j].trim());
        j++;
      }
      const isMapping = collected.some((l) => /^[A-Za-z0-9_-]+:/.test(l));
      if (isMapping || rest === "") {
        fields.__parent = key;
        fields[key] = "__mapping__";
        continue; // let the nested branch pick up children
      }
      fields[key] = collected.join(" ").trim();
      i = j - 1;
      continue;
    }
    fields.__parent = null;
    fields[key] = rest.replace(/^["']|["']$/g, "").trim();
  }
  delete fields.__parent;
  return { fields, body };
}

function lintSkill(name) {
  const dir = join(SKILLS_DIR, name);
  const skillFile = join(dir, "SKILL.md");

  if (!existsSync(skillFile)) {
    err(name, "no SKILL.md in the skill folder");
    return;
  }

  const raw = readFileSync(skillFile, "utf8");
  const { fields, body } = parseFrontmatter(raw, name);

  // --- required by the spec ---
  if (!fields.name) err(name, "frontmatter is missing required field: name");
  else if (fields.name !== name)
    err(name, `frontmatter name "${fields.name}" does not match folder name "${name}"`);

  if (!fields.description) {
    err(name, "frontmatter is missing required field: description");
  } else {
    if (fields.description.length > MAX_DESC)
      err(name, `description is ${fields.description.length} chars (max ${MAX_DESC})`);
    if (!/\b(use|apply|run)\s+(when|it|whenever|before|at the beginning)\b/i.test(fields.description))
      warn(name, 'description has no "Use when..." trigger clause');
  }

  // --- house rules ---
  if (!fields.license) warn(name, "no license field (set the SPDX id)");
  if (!fields["metadata.version"]) warn(name, "no metadata.version");
  if (!fields["metadata.author"]) warn(name, "no metadata.author");
  if (!fields.compatibility) warn(name, "no compatibility field (list tools, runtimes, OS)");

  // Vendored skills must ship their upstream license.
  if (fields["metadata.vendored-from"] && !existsSync(join(dir, "LICENSE")))
    err(name, `vendored from ${fields["metadata.vendored-from"]} but has no LICENSE file`);

  // --- body checks ---
  const bodyLines = body.split(/\r?\n/).length;
  if (bodyLines > MAX_BODY_LINES)
    warn(name, `body is ${bodyLines} lines (over ${MAX_BODY_LINES}); move detail into references/`);

  // Only fenced code blocks carry executable instructions. Prose is free to
  // discuss ./scripts/ as an anti-pattern without tripping these checks.
  const code = [...body.matchAll(/```[\w-]*[^\n]*\n([\s\S]*?)```/g)]
    .map((m) => m[1])
    .join("\n");

  // Bare relative script paths break once a skill is installed elsewhere.
  const badPaths = [...code.matchAll(/(?:^|[\s`("'])\.\/(scripts|references)\//gm)];
  if (badPaths.length)
    err(name, `${badPaths.length} bare relative path(s) like ./scripts/ in a code block - resolve the skill dir first`);

  // Every referenced local script should exist.
  for (const m of code.matchAll(/(?:scripts\/[\w.-]+\.(?:sh|py|mjs|js))/g)) {
    const rel = m[0];
    if (!existsSync(join(dir, rel)) && !existsSync(join(ROOT, rel)))
      warn(name, `code block references ${rel}, which was not found in the skill folder`);
  }

  return fields;
}

// --- run ---
if (!existsSync(SKILLS_DIR)) {
  console.error("No skills/ directory at the repo root.");
  process.exit(1);
}

const names = readdirSync(SKILLS_DIR)
  .filter((n) => !n.startsWith(".") && statSync(join(SKILLS_DIR, n)).isDirectory())
  .sort();

if (!names.length) {
  console.error("skills/ contains no skill folders.");
  process.exit(1);
}

const seen = new Map();
for (const n of names) {
  const f = lintSkill(n);
  if (f?.description) seen.set(n, f.description.toLowerCase());
}

// Overlapping triggers make invocation unreliable; flag near-duplicate descriptions.
const entries = [...seen.entries()];
for (let i = 0; i < entries.length; i++) {
  for (let j = i + 1; j < entries.length; j++) {
    const a = new Set(entries[i][1].split(/\W+/).filter((w) => w.length > 5));
    const b = new Set(entries[j][1].split(/\W+/).filter((w) => w.length > 5));
    if (!a.size || !b.size) continue;
    const overlap = [...a].filter((w) => b.has(w)).length / Math.min(a.size, b.size);
    if (overlap > 0.75)
      warnings.push(
        `${entries[i][0]} / ${entries[j][0]}: descriptions overlap ${Math.round(overlap * 100)}% - triggers may compete`
      );
  }
}

console.log(`Linted ${names.length} skills: ${names.join(", ")}\n`);
for (const w of warnings) console.log(`  warn   ${w}`);
for (const e of errors) console.log(`  ERROR  ${e}`);

if (errors.length) {
  console.log(`\n${errors.length} error(s), ${warnings.length} warning(s). Failed.`);
  process.exit(1);
}
if (STRICT && warnings.length) {
  console.log(`\n${warnings.length} warning(s) and --strict is set. Failed.`);
  process.exit(1);
}
console.log(`\nOK. 0 errors, ${warnings.length} warning(s).`);
