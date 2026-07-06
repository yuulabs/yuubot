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

export { BASE };

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });
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
    throw new ApiRequestError(response.status, message, code, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
