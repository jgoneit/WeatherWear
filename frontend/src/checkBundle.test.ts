// @vitest-environment node
/**
 * check:bundle VAPID key forms (Phase 2 Req 18.8, review fix 3).
 *
 * A throwaway P-256 key is generated with WebCrypto for every run, so no key
 * is ever committed. A bundle that carries the private scalar `d` as JWK
 * base64url, standard base64 or hex — not only the PEM body — must be caught.
 */
import { beforeAll, describe, expect, it } from "vitest";
import {
  BASE64_WINDOW,
  HEX_WINDOW,
  containsAny,
  pemBody,
  secretNeedles,
} from "../scripts/vapid-secret-forms.mjs";

let pem = "";
let jwkD = "";
let jwkX = "";
let needles: string[] = [];

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

beforeAll(async () => {
  const pair = (await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
  const lines = base64(pkcs8).match(/.{1,64}/g) ?? [];
  pem = ["-----BEGIN " + "PRIVATE KEY-----", ...lines, "-----END " + "PRIVATE KEY-----", ""].join("\n");
  const jwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
  jwkD = jwk.d ?? "";
  jwkX = jwk.x ?? "";
  needles = secretNeedles(pem);
});

describe("VAPID private key needles", () => {
  it("has no needles without a key", () => {
    expect(secretNeedles("")).toEqual([]);
  });

  it("catches the PEM body, as before", () => {
    const body = pemBody(pem);
    expect(containsAny(`const k = "${body.slice(20, 20 + BASE64_WINDOW)}";`, needles)).toBe(true);
  });

  it("catches the private scalar as JWK base64url", () => {
    expect(jwkD).toHaveLength(43);
    expect(containsAny(`{"kty":"EC","crv":"P-256","d":"${jwkD}"}`, needles)).toBe(true);
    expect(containsAny(`x${jwkD.slice(5, 5 + BASE64_WINDOW)}x`, needles)).toBe(true); // a partial copy
  });

  it("catches the private scalar as standard base64", () => {
    const d = fromBase64Url(jwkD);
    expect(containsAny(`atob("${base64(d)}")`, needles)).toBe(true);
  });

  it("catches the private scalar as hex in either case", () => {
    const d = hex(fromBase64Url(jwkD));
    expect(d).toHaveLength(64);
    expect(containsAny(`0x${d}`, needles)).toBe(true);
    expect(containsAny(d.toUpperCase().slice(10, 10 + HEX_WINDOW), needles)).toBe(true);
  });

  it("does not flag an unrelated bundle or the public key", () => {
    expect(containsAny("export const a = 1; // ordinary bundle text 0123456789abcdef", needles)).toBe(false);
    expect(containsAny(jwkX, needles)).toBe(false);
  });
});
