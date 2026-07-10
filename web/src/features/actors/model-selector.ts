import type { ModelSelector } from "@/shared/types/api";

export function formatModelSelector(selector: ModelSelector | null): string {
  if (!selector) return "unset";
  return selector.type === "alias"
    ? `Alias: ${selector.alias}`
    : `${selector.endpoint_id}/${selector.model}`;
}
