/**
 * 프론트엔드 환경 설정.
 * VITE_API_BASE_URL만 참조한다. 다른 .env 값(특히 GEMINI_*)은 번들에 들어가면 안 된다 (Req 16.6).
 * 값이 없으면 같은 출처("")로 요청한다.
 */
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

/** 데모 관리 화면과 탭은 개발 모드에서만 노출한다 (Req 14.1, 14.2). */
export const IS_DEV: boolean = import.meta.env.DEV;
