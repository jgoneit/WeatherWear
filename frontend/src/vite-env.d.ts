/// <reference types="vite/client" />

// 프론트엔드가 읽는 환경 변수는 VITE_API_BASE_URL 하나뿐이다 (Req 16.6).
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}
