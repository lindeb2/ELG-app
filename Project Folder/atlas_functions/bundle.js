/**
 * Builds commitLog.js — the single file deployed to Atlas App Services.
 * Run from this directory: node bundle.js
 */
const fs = require("fs");
const path = require("path");

const dir = __dirname;

function read(name) {
  return fs.readFileSync(path.join(dir, name), "utf8");
}

function stripModuleExports(content) {
  return content.replace(/\nmodule\.exports\s*=\s*\{[\s\S]*?\};\s*$/m, "\n");
}

/** Remove local sibling imports (not valid in Atlas Functions). */
function stripLocalRequires(content) {
  return content.replace(
    /const\s*\{[\s\S]*?\}\s*=\s*require\("\.\/[^"]+"\);\s*\n/g,
    ""
  );
}

function stripLeadingFileComment(content) {
  return content.replace(/^\/\*\*[\s\S]*?\*\/\s*\n+/, "");
}

const header = `/**
 * Atlas App Services Function — DEPLOY THIS FILE ONLY.
 *
 * In Atlas: App Services → Functions → create function named "commitLog"
 * The filename must match the function name (commitLog.js).
 *
 * Do not use require("./…") for sibling files — Atlas does not support that pattern.
 * This file is generated from the source modules below. Regenerate after edits:
 *   node bundle.js
 *
 * Source modules (repo only, not deployed):
 *   - commit_pipelines.js
 *   - highscore_logic.js
 *   - commit_log.js
 */

`;

function prepareSource(filename, sectionLabel) {
  let content = read(filename);
  content = stripLeadingFileComment(content);
  content = stripLocalRequires(content);
  content = stripModuleExports(content);
  return `/* --- ${sectionLabel} --- */\n\n${content.trim()}\n`;
}

const output =
  header +
  prepareSource("commit_pipelines.js", "commit_pipelines") +
  "\n" +
  prepareSource("highscore_logic.js", "highscore_logic") +
  "\n" +
  prepareSource("commit_log.js", "commit orchestrator") +
  "\n";

fs.writeFileSync(path.join(dir, "commitLog.js"), output);
console.log("Wrote commitLog.js");
