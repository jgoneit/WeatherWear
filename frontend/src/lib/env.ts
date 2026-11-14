/**
 * 프론트엔드 환경 설정.
 * 공개 설정인 API 주소와 데모 표시 플래그만 참조한다. 비밀 키는 번들에 넣지 않는다.
 * 값이 없으면 같은 출처("")로 요청한다.
 */
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

/** 데모 관리 화면과 탭은 개발 모드에서만 노출한다 (Req 14.1, 14.2). */
export const IS_DEV: boolean = import.meta.env.DEV;

/** Public fixture demo only; this never exposes the development admin panel. */
export const IS_PUBLIC_DEMO: boolean = import.meta.env.VITE_PUBLIC_DEMO === "true";
