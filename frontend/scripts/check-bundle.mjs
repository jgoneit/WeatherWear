#!/usr/bin/env node
// Bundle secret check (Req 16.6).
// Scans every file under dist/ for:
//   1. the uppercase string "GEMINI" (catches env names such as GEMINI_API_KEY);
//   2. the actual GEMINI_API_KEY value, if one is set in the environment or in
//      the repository-root .env file.
// The check is case-sensitive on purpose: the bundle legitimately contains the
// API field `gemini_api_key_configured` and the UI label "Gemini API 키".
// Exit 0: nothing found. Exit 1: a match was found, or dist/ is missing/empty.
// Only file names are reported; matched content and the key are never printed.
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const NEEDLE = "GEMINI";
const MIN_KEY_LENGTH = 8;
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(frontendRoot, "..");
const distDir = path.resolve(frontendRoot, process.argv[2] ?? "dist");

async function listFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listFiles(full)));
    } else if (entry.isFile()) {
      files.push(full);
    }
  }
  return files;
}

// Reads GEMINI_API_KEY from the process environment, else from <repo>/.env.
// Returns "" when no usable key is configured.
async function loadApiKey() {
  let value = process.env.GEMINI_API_KEY ?? "";
  if (!value) {
    try {
      const text = await readFile(path.join(repoRoot, ".env"), "utf8");
      for (const line of text.split(/\r?\n/)) {
        const match = line.match(/^\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*)$/);
        if (match) value = match[1];
      }
    } catch {
      // No .env file: only the name check applies.
    }
  }
  value = value.trim().replace(/^(['"])(.*)\1$/, "$2").trim();
  return value.length >= MIN_KEY_LENGTH ? value : "";
}

async function main() {
  try {
    if (!(await stat(distDir)).isDirectory()) throw new Error("not a directory");
  } catch {
    console.error(`check:bundle: build output not found (${path.basename(distDir)}/). Run vite build first.`);
    return 1;
  }

  const files = await listFiles(distDir);
  if (files.length === 0) {
    console.error(`check:bundle: ${path.basename(distDir)}/ is empty.`);
    return 1;
  }

  const apiKey = await loadApiKey();
  const nameHits = [];
  const keyHits = [];
  for (const file of files) {
    // latin1 maps each byte to one char, so binary files are scanned too.
    const text = (await readFile(file)).toString("latin1");
    const name = path.relative(distDir, file);
    if (text.includes(NEEDLE)) nameHits.push(name);
    if (apiKey && text.includes(apiKey)) keyHits.push(name);
  }

  if (nameHits.length > 0 || keyHits.length > 0) {
    if (nameHits.length > 0) {
      console.error(`check:bundle: forbidden string "${NEEDLE}" found in ${nameHits.length} file(s):`);
      for (const name of nameHits.sort()) console.error(`  - ${name}`);
    }
    if (keyHits.length > 0) {
      console.error(`check:bundle: GEMINI_API_KEY value found in ${keyHits.length} file(s):`);
      for (const name of keyHits.sort()) console.error(`  - ${name}`);
    }
    return 1;
  }

  const keyNote = apiKey ? "configured key value checked" : "no key configured, value check skipped";
  console.log(
    `check:bundle: OK, scanned ${files.length} file(s) in ${path.basename(distDir)}/, no "${NEEDLE}" found (${keyNote}).`,
  );
  return 0;
}

process.exitCode = await main();
