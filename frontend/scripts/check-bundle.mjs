#!/usr/bin/env node
// Bundle secret check (Phase 1 Req 16.6, Phase 2 Req 18.8).
// Scans every file under dist/ for:
//   1. the uppercase string "GEMINI" (catches env names such as GEMINI_API_KEY);
//   2. the actual GEMINI_API_KEY value, if one is set in the environment or in
//      the repository-root .env file;
//   3. the string "PRIVATE KEY" (catches any PEM private key block);
//   4. the VAPID private key: when VAPID_KEY_DIR (process environment, then the
//      repository-root .env, then the default data/secrets) holds vapid_private.pem,
//      every 16-character window of its base64 body, and the private scalar `d` as
//      base64url (JWK), base64 and hex (see vapid-secret-forms.mjs), are searched for.
// The checks are case-sensitive on purpose: the bundle legitimately contains the
// API field `gemini_api_key_configured` and the UI label "Gemini API 키".
// Exit 0: nothing found. Exit 1: a match was found, or dist/ is missing/empty.
// Only file names are reported; matched content, key values and paths are never printed.
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { containsAny, secretNeedles } from "./vapid-secret-forms.mjs";

const NEEDLE = "GEMINI";
const PEM_NEEDLE = "PRIVATE KEY";
const MIN_KEY_LENGTH = 8;
const VAPID_PRIVATE_FILE = "vapid_private.pem";
const DEFAULT_VAPID_KEY_DIR = "data/secrets";
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

function unquote(value) {
  return value.trim().replace(/^(['"])(.*)\1$/, "$2").trim();
}

// Value of `name` in <repo>/.env (last assignment wins), or "" when absent.
async function readDotenv(name) {
  let value = "";
  try {
    const text = await readFile(path.join(repoRoot, ".env"), "utf8");
    const pattern = new RegExp(`^\\s*(?:export\\s+)?${name}\\s*=\\s*(.*)$`);
    for (const line of text.split(/\r?\n/)) {
      const match = line.match(pattern);
      if (match) value = match[1];
    }
  } catch {
    // No .env file.
  }
  return unquote(value);
}

// Reads GEMINI_API_KEY from the process environment, else from <repo>/.env.
// Returns "" when no usable key is configured.
async function loadApiKey() {
  let value = unquote(process.env.GEMINI_API_KEY ?? "");
  if (!value) value = await readDotenv("GEMINI_API_KEY");
  return value.length >= MIN_KEY_LENGTH ? value : "";
}

// Same order as the backend Config_Loader: process environment > .env > default.
// A relative directory is resolved against the repository root.
async function vapidKeyDir() {
  let dir = unquote(process.env.VAPID_KEY_DIR ?? "");
  if (!dir) dir = await readDotenv("VAPID_KEY_DIR");
  if (!dir) dir = DEFAULT_VAPID_KEY_DIR;
  return path.resolve(repoRoot, dir);
}

// Text of <VAPID_KEY_DIR>/vapid_private.pem, or "" when it does not exist or cannot be read.
async function loadVapidPem() {
  try {
    return await readFile(path.join(await vapidKeyDir(), VAPID_PRIVATE_FILE), "utf8");
  } catch {
    return "";
  }
}

function report(label, hits) {
  if (hits.length === 0) return;
  console.error(`check:bundle: ${label} found in ${hits.length} file(s):`);
  for (const name of hits.sort()) console.error(`  - ${name}`);
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
  const vapidWindows = secretNeedles(await loadVapidPem());
  const nameHits = [];
  const keyHits = [];
  const pemHits = [];
  const vapidHits = [];
  for (const file of files) {
    // latin1 maps each byte to one char, so binary files are scanned too.
    const text = (await readFile(file)).toString("latin1");
    const name = path.relative(distDir, file);
    if (text.includes(NEEDLE)) nameHits.push(name);
    if (apiKey && text.includes(apiKey)) keyHits.push(name);
    if (text.includes(PEM_NEEDLE)) pemHits.push(name);
    if (containsAny(text, vapidWindows)) vapidHits.push(name);
  }

  if (nameHits.length + keyHits.length + pemHits.length + vapidHits.length > 0) {
    report(`forbidden string "${NEEDLE}"`, nameHits);
    report("GEMINI_API_KEY value", keyHits);
    report(`forbidden string "${PEM_NEEDLE}"`, pemHits);
    report("VAPID private key content", vapidHits);
    return 1;
  }

  const keyNote = apiKey ? "configured key value checked" : "no key configured, value check skipped";
  const vapidNote = vapidWindows.length > 0 ? "VAPID private key checked" : "no VAPID private key, key check skipped";
  console.log(
    `check:bundle: OK, scanned ${files.length} file(s) in ${path.basename(distDir)}/, no "${NEEDLE}" or "${PEM_NEEDLE}" found (${keyNote}; ${vapidNote}).`,
  );
  return 0;
}

process.exitCode = await main();
