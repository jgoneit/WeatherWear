/// <reference types="vite/client" />

// Public build-time configuration only; never add secrets here.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_PUBLIC_DEMO?: string;
}
