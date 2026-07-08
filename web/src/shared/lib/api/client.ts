export class ApiRequestError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly detail?: Record<string, unknown>;

  constructor(status: number, message: string, code?: string, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

const BASE = "/api";
const CSRF_STORAGE_KEY = "yuubot:csrf-token";

export { BASE };

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = body.error && typeof body.error === "object" ? body.error as Record<string, unknown> : null;
    const message = typeof error?.message === "string"
      ? error.message
      : typeof body.detail === "string"
        ? body.detail
        : typeof body.message === "string"
          ? body.message
          : typeof body.reason === "string"
            ? body.reason
            : response.statusText;
    const code = typeof error?.code === "string" ? error.code : undefined;
    const detail = error?.detail && typeof error.detail === "object" && !Array.isArray(error.detail)
      ? error.detail as Record<string, unknown>
      : undefined;
    if (response.status === 401 || (response.status === 403 && code === "forbidden")) {
      redirectToLogin();
    }
    throw new ApiRequestError(response.status, message, code, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function authenticatedFetch(url: string, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? "GET").toUpperCase();
  const csrfToken = csrfTokenForRequest(method);
  const response = await fetch(url, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...init?.headers,
    },
  });
  if (response.status === 401 || response.status === 403) {
    redirectToLogin();
  }
  return response;
}

export function setCsrfToken(token: string): void {
  localStorage.setItem(CSRF_STORAGE_KEY, token);
}

export function clearCsrfToken(): void {
  localStorage.removeItem(CSRF_STORAGE_KEY);
}

export function getCsrfToken(): string | null {
  return localStorage.getItem(CSRF_STORAGE_KEY);
}

function csrfTokenForRequest(method: string): string | null {
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    return null;
  }
  return getCsrfToken();
}

function redirectToLogin(): void {
  if (window.location.pathname === "/login") {
    return;
  }
  clearCsrfToken();
  const redirect = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  window.location.assign(`/login?redirect=${encodeURIComponent(redirect)}`);
}
