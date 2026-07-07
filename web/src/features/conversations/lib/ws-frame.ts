export function shouldProcessCommandFrame(
  frameId: string | undefined,
  activeCommandId: string | null,
): boolean {
  if (activeCommandId && frameId && frameId !== activeCommandId) {
    return false;
  }
  return true;
}

export function shouldProcessConversationFrame(
  frameConversationId: string | undefined,
  subscribedConversationId: string | null,
  frameId: string | undefined,
  activeCommandId: string | null,
): boolean {
  if (subscribedConversationId && frameConversationId && frameConversationId !== subscribedConversationId) {
    return false;
  }
  return shouldProcessCommandFrame(frameId, activeCommandId);
}
