/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional absolute backend URL for cross-origin dev servers, e.g. "http://127.0.0.1:8000/". */
  readonly VITE_API_BASE?: string;
}
