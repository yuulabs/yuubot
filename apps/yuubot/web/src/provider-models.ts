export interface ProviderModelOption {
  id: string;
  displayName?: string;
}

export interface ProviderModelRequest {
  backendId: string;
  providerKey: string;
  baseUrl: string;
  apiKey?: string;
}

export interface ProviderValidationResult {
  valid: boolean;
  detail: string;
  recommended_model_valid: boolean;
  models: ProviderModelOption[];
  capabilities: Record<string, boolean>;
}

interface ProviderModelsResponse {
  data?: unknown;
  models?: unknown;
}

interface ProviderValidationResponse {
  data?: {
    valid?: unknown;
    detail?: unknown;
    recommended_model_valid?: unknown;
    models?: unknown;
    capabilities?: unknown;
  };
}

export async function fetchProviderModels({
  backendId,
  providerKey,
  baseUrl,
  apiKey,
}: ProviderModelRequest): Promise<ProviderModelOption[]> {
  const baseUrlWarning = providerBaseUrlWarning(providerKey, baseUrl);
  if (baseUrlWarning) {
    throw new Error(baseUrlWarning);
  }

  const response = await fetch(`/api/providers/${backendId}/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_url: baseUrl,
      ...(apiKey?.trim() ? { api_key: apiKey } : {}),
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(modelFetchErrorMessage(body, response.status));
  }

  const body = (await response.json()) as ProviderModelsResponse;
  return parseProviderModels(body);
}

export async function validateProvider({
  backendId,
  providerKey,
  baseUrl,
  apiKey,
}: ProviderModelRequest): Promise<ProviderValidationResult> {
  const baseUrlWarning = providerBaseUrlWarning(providerKey, baseUrl);
  if (baseUrlWarning) {
    throw new Error(baseUrlWarning);
  }

  const response = await fetch(`/api/providers/${backendId}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_url: baseUrl,
      ...(apiKey?.trim() ? { api_key: apiKey } : {}),
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(modelFetchErrorMessage(body, response.status));
  }

  const body = (await response.json()) as ProviderValidationResponse;
  return parseProviderValidation(body);
}

export function providerBaseUrlWarning(providerKey: string, baseUrl: string): string {
  if (providerKey !== "deepseek") {
    return "";
  }
  const url = parseUrl(baseUrl);
  if (!url || url.hostname !== "api.deepseek.com") {
    return "";
  }
  const path = url.pathname.replace(/\/+$/, "");
  if (path !== "/v1" && !path.startsWith("/v1/")) {
    return "";
  }
  return "DeepSeek Base URL should be https://api.deepseek.com. Remove /v1 from the URL.";
}

export function mergeModelOptions(
  ...groups: Array<Array<ProviderModelOption | string | undefined>>
): ProviderModelOption[] {
  const seen = new Set<string>();
  const models: ProviderModelOption[] = [];
  for (const group of groups) {
    for (const item of group) {
      const model = normalizeModelOption(item);
      if (!model || seen.has(model.id)) {
        continue;
      }
      seen.add(model.id);
      models.push(model);
    }
  }
  return models.sort((a, b) => a.id.localeCompare(b.id));
}

function modelFetchErrorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) {
      return detail;
    }
  }
  return `Model fetch returned HTTP ${status}.`;
}

function parseUrl(value: string): URL | undefined {
  try {
    return new URL(value.trim());
  } catch {
    return undefined;
  }
}

function parseProviderModels(body: ProviderModelsResponse): ProviderModelOption[] {
  const rawModels = Array.isArray(body.data)
    ? body.data
    : Array.isArray(body.models)
      ? body.models
      : [];
  return mergeModelOptions(rawModels.map(modelFromProviderItem));
}

function parseProviderValidation(
  body: ProviderValidationResponse,
): ProviderValidationResult {
  const data = body.data ?? {};
  return {
    valid: data.valid === true,
    detail: typeof data.detail === "string" ? data.detail : "",
    recommended_model_valid: data.recommended_model_valid === true,
    models: parseProviderModels({ data: data.models }),
    capabilities:
      data.capabilities && typeof data.capabilities === "object"
        ? (data.capabilities as Record<string, boolean>)
        : {},
  };
}

function modelFromProviderItem(item: unknown): ProviderModelOption | undefined {
  if (!item || typeof item !== "object") {
    return undefined;
  }
  const record = item as Record<string, unknown>;
  const id = String(record.id ?? record.name ?? "").trim();
  if (!id) {
    return undefined;
  }
  const displayName = String(record.display_name ?? record.displayName ?? "").trim();
  return { id, displayName: displayName || undefined };
}

function normalizeModelOption(
  item: ProviderModelOption | string | undefined,
): ProviderModelOption | undefined {
  if (!item) {
    return undefined;
  }
  if (typeof item === "string") {
    const id = item.trim();
    return id ? { id } : undefined;
  }
  const id = item.id.trim();
  return id ? { ...item, id } : undefined;
}
