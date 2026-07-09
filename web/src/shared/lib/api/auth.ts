import { BASE, clearCsrfToken, request, setCsrfToken } from "./client";

export interface LoginResponse {
  csrf_token: string;
}

export interface SessionResponse {
  user_id: string;
  display_name?: string | null;
  csrf_token: string;
  created_at: number;
  last_seen_at: number;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await request<LoginResponse>(`${BASE}/auth/login`, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setCsrfToken(response.csrf_token);
  return response;
}

export async function loadSession(): Promise<SessionResponse> {
  const response = await request<SessionResponse>(`${BASE}/auth/session`);
  setCsrfToken(response.csrf_token);
  return response;
}

export async function logout(): Promise<void> {
  try {
    await request<{ logged_out: boolean }>(`${BASE}/auth/logout`, { method: "POST" });
  } finally {
    clearCsrfToken();
  }
}
