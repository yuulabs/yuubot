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
    const detail = error?.message ?? body.detail ?? body.message ?? body.reason ?? response.statusText;
    throw new Error(`${response.status} ${detail}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
