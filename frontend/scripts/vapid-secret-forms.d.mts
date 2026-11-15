// Types for vapid-secret-forms.mjs (used by src/checkBundle.test.ts).
export declare const BASE64_WINDOW: number;
export declare const HEX_WINDOW: number;
export declare function pemBody(pemText: string): string;
export declare function privateScalar(pemText: string): Uint8Array | null;
export declare function secretNeedles(pemText: string): string[];
export declare function containsAny(text: string, needles: string[]): boolean;
