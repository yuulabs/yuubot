import { ApiRequestError } from "@/shared/lib/api/client";

export function describeApiError(err: unknown): string {
  if (err instanceof ApiRequestError) {
    switch (err.code) {
      case "model_pricing_required": {
        const selector = typeof err.detail?.selector === "string" ? err.detail.selector : "this model";
        return `Model "${selector}" has no pricing yet. Open the provider page, select the model, set per-million prices (0 is allowed), save the model card, then try again.`;
      }
      case "model_selector_not_found": {
        const selector = typeof err.detail?.selector === "string" ? err.detail.selector : "that model";
        return `Model "${selector}" is not in the provider catalog. Refresh the catalog on the provider page or choose another model.`;
      }
      case "configuration_required":
        return err.message;
      case "not_found":
        return err.message;
      default:
        return err.message;
    }
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}
