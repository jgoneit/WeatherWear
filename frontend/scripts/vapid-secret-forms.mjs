// VAPID private key forms searched for by check-bundle.mjs (Phase 2 Req 18.8, review fix 3).
//
// A leaked key does not have to look like the PEM file. Besides 16-character
// windows of the PEM base64 body, the private scalar `d` (32 bytes for P-256)
// is searched for in the encodings code would most likely use:
//   - base64url (the JWK `d` member) and standard base64: 16-character windows (12 bytes)
//   - hex, lower and upper case: 24-character windows (12 bytes)
// Windows catch a partial copy too. The needles are only compared, never printed.
import { createPrivateKey } from "node:crypto";

export const BASE64_WINDOW = 16;
export const HEX_WINDOW = 24;

function windows(text, size, into) {
  for (let i = 0; i + size <= text.length; i += 1) into.add(text.slice(i, i + size));
}

/** Base64 body of a PEM file: armour lines and whitespace removed. */
export function pemBody(pemText) {
  return pemText
    .split(/\r?\n/)
    .filter((line) => !line.startsWith("-----"))
    .join("")
    .replace(/[^A-Za-z0-9+/=]/g, "");
}

/** The private scalar `d` as bytes, or null when the PEM is not an EC private key Node can read. */
export function privateScalar(pemText) {
  try {
    const jwk = createPrivateKey(pemText).export({ format: "jwk" });
    return typeof jwk.d === "string" ? Buffer.from(jwk.d, "base64url") : null;
  } catch {
    return null;
  }
}

/** Every string whose presence in the bundle means the private key leaked. Empty for no key. */
export function secretNeedles(pemText) {
  const needles = new Set();
  if (!pemText) return [];
  windows(pemBody(pemText), BASE64_WINDOW, needles);
  const d = privateScalar(pemText);
  if (d !== null && d.length > 0) {
    windows(d.toString("base64url"), BASE64_WINDOW, needles);
    windows(d.toString("base64"), BASE64_WINDOW, needles);
    const hex = d.toString("hex");
    windows(hex, HEX_WINDOW, needles);
    windows(hex.toUpperCase(), HEX_WINDOW, needles);
  }
  return [...needles];
}

/** True when `text` contains any needle. */
export function containsAny(text, needles) {
  return needles.some((needle) => text.includes(needle));
}
