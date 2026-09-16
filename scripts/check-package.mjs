#!/usr/bin/env node
/**
 * Verifies the npm tarball before it ships.
 *
 * Every vendored skill must carry its upstream LICENSE, and that LICENSE must
 * survive into the package. PolyForm Shield clause 2 and the MIT terms both
 * require the notice to travel with the code.
 *
 * Usage: node scripts/check-package.mjs
 */
import { execSync } from "node:child_process";
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SKILLS_DIR = join(ROOT, "skills");
const JUNK = ["node_modules/", "__pycache__/", ".git/", ".artifacts/", ".env"];

const errors = [];

/** Read only the frontmatter block. Prose that mentions a key is not that key. */
function frontmatter(file) {
  const m = readFileSync(file, "utf8").match(/^---\r?\n([\s\S]*?)\r?\n---/);
  return m ? m[1] : "";
}

// A fixed command string: npm is a .cmd shim on Windows that Node 24 refuses to
// spawn directly, and there is no user input here that would need escaping.
const files = JSON.parse(
  execSync("npm pack --dry-run --json", {
    cwd: ROOT,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  })
)[0].files.map((f) => f.path.replace(/\\/g, "/"));

const skills = readdirSync(SKILLS_DIR).filter(
  (n) => !n.startsWith(".") && statSync(join(SKILLS_DIR, n)).isDirectory()
);

let vendored = 0;
for (const name of skills) {
  const dir = join(SKILLS_DIR, name);
  if (!/^\s*vendored-from:/m.test(frontmatter(join(dir, "SKILL.md")))) continue;
  vendored++;

  if (!existsSync(join(dir, "LICENSE"))) {
    errors.push(`${name} declares vendored-from but has no LICENSE file`);
    continue;
  }
  if (!files.includes(`skills/${name}/LICENSE`)) {
    errors.push(`${name}: LICENSE exists but is missing from the npm tarball`);
  }
}

for (const pattern of JUNK) {
  const hit = files.find((f) => f.includes(pattern));
  if (hit) errors.push(`${pattern} leaked into the package (${hit})`);
}

if (!files.includes("NOTICE.md")) errors.push("NOTICE.md is missing from the package");
if (!files.includes("LICENSE")) errors.push("root LICENSE is missing from the package");

console.log(`Checked ${files.length} packaged files across ${skills.length} skills.`);
console.log(`${vendored} vendored skill(s), each carrying its upstream LICENSE.`);

if (errors.length) {
  for (const e of errors) console.log(`  ERROR  ${e}`);
  console.log(`\n${errors.length} error(s). Failed.`);
  process.exit(1);
}
console.log("Package contents OK.");
