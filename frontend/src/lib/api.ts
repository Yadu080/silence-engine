// Thin fetch wrapper.
//
// URLs are RELATIVE ("api/health", not "/api/health") so the same build works when the
// backend serves it at "/", behind Jupyter's "/proxy/8000/", or behind any reverse-proxy
// prefix. Set VITE_API_BASE (e.g. "http://127.0.0.1:8000/") only when a frontend dev server
// runs on another origin; the backend must then list that origin in SILENCE_CORS_ORIGINS.
// Every write request sends the anti-CSRF header the backend requires.

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const TOKEN_KEY = "silence-api-token";
const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
export const CSRF_HEADER = "X-Requested-With";

/** Resolve an API path ("/api/x" or "api/x") against the configured base. */
export function apiUrl(path: string): string {
  const rel = path.replace(/^\/+/, "");
  if (!API_BASE) return rel;
  return API_BASE.endsWith("/") ? `${API_BASE}${rel}` : `${API_BASE}/${rel}`;
}

export function getToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token simply isn't remembered */
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const isWrite = !!init.method && init.method !== "GET";
  const token = getToken();
  if (isWrite) headers.set(CSRF_HEADER, "SilenceEngine");
  if (token && isWrite) headers.set("Authorization", `Bearer ${token}`);
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...init, headers });
  } catch {
    throw new ApiError(0, "network_error", "The backend could not be reached. Is the Flask server running?");
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (${res.status})`, err?.details);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data ?? {}),
    }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** Ask the shell to re-poll /api/health now (e.g. right after starting a background job). */
export function requestHealthRefresh(): void {
  window.dispatchEvent(new Event("silence:health"));
}
