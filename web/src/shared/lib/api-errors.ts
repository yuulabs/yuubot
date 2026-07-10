import { ApiRequestError } from "@/shared/lib/api/client";

export interface WsErrorPayload {
  code?: string;
  message?: string;
  detail?: Record<string, unknown>;
}

export function describeWsError(error: WsErrorPayload | undefined): string {
  if (!error) {
    return "Conversation request failed.";
  }
  const message = error.message?.trim();
  if (message) {
    return message;
  }
  switch (error.code) {
    case "conversation_busy":
      return "Conversation is already running. Wait for the current turn to finish or interrupt it.";
    case "conversation_blocked": {
      const reason = typeof error.detail?.reason === "string" ? error.detail.reason : "";
      return reason
        ? `Conversation blocked: ${reason}.`
        : "Conversation blocked before the turn could finish.";
    }
    case "internal_error":
      return "The server failed while running this conversation turn.";
    case "bad_request":
      return "The conversation request was invalid.";
    case "not_found":
      return "Conversation or actor was not found.";
    default:
      return error.code ? `Conversation request failed (${error.code}).` : "Conversation request failed.";
  }
}

export function describeConversationError(lastError: Record<string, unknown> | null | undefined): string {
  if (!lastError) {
    return "";
  }
  const reason = lastError.reason;
  if (typeof reason === "string" && reason) {
    return `Conversation blocked: ${reason}.`;
  }
  const message = lastError.message;
  if (typeof message === "string" && message) {
    return message;
  }
  return JSON.stringify(lastError, null, 2);
}

export function describeApiError(err: unknown): string {
  if (err instanceof ApiRequestError) {
    switch (err.code) {
      case "gateway_modality_unavailable":
        return err.message;
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
