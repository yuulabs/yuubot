import type {
  AccountSnapshot,
  ItemsResponse,
  ModelCard,
  ModelCardInput,
  ProviderDetail,
  ProviderInput,
  ProviderProtocolSpec,
  ProviderSnapshot,
  ValidationResult,
} from "@/shared/types/api";
import { BASE, request } from "./client";

export function listProviderProtocols(): Promise<ProviderProtocolSpec[]> {
  return request<ItemsResponse<ProviderProtocolSpec>>(`${BASE}/provider-protocols`).then((res) => res.items);
}

export function listProviders(): Promise<ProviderSnapshot[]> {
  return request<ItemsResponse<ProviderSnapshot>>(`${BASE}/providers`).then((res) => res.items);
}

export function getProvider(providerId: string): Promise<ProviderDetail> {
  return request<ProviderDetail>(`${BASE}/providers/${encodeURIComponent(providerId)}`);
}

export function putProvider(providerId: string, input: ProviderInput): Promise<ProviderSnapshot> {
  return request<ProviderSnapshot>(`${BASE}/providers/${encodeURIComponent(providerId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteProvider(providerId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/providers/${encodeURIComponent(providerId)}`, { method: "DELETE" });
}

export function validateProvider(providerId: string): Promise<ValidationResult> {
  return request<ValidationResult>(`${BASE}/providers/${encodeURIComponent(providerId)}/validate`, { method: "POST" });
}

export function getProviderBalance(providerId: string): Promise<AccountSnapshot> {
  return request<AccountSnapshot>(`${BASE}/providers/${encodeURIComponent(providerId)}/balance`);
}

export function refreshProviderCatalog(providerId: string): Promise<{ model_cards: ModelCard[] }> {
  return request<{ model_cards: ModelCard[] }>(`${BASE}/providers/${encodeURIComponent(providerId)}/catalog/refresh`, { method: "POST" });
}

export function listProviderModelCards(providerId: string): Promise<ModelCard[]> {
  return request<ItemsResponse<ModelCard>>(`${BASE}/providers/${encodeURIComponent(providerId)}/model-cards`).then((res) => res.items);
}

export function putProviderModelCard(providerId: string, selector: string, input: ModelCardInput): Promise<ModelCard> {
  return request<ModelCard>(`${BASE}/providers/${encodeURIComponent(providerId)}/model-cards/${encodeURIComponent(selector)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteProviderModelCard(
  providerId: string,
  selector: string,
): Promise<{ provider_id: string; selector: string; deleted: boolean }> {
  return request<{ provider_id: string; selector: string; deleted: boolean }>(
    `${BASE}/providers/${encodeURIComponent(providerId)}/model-cards/${encodeURIComponent(selector)}`,
    { method: "DELETE" },
  );
}
