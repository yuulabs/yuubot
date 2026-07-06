export function shouldProcessCommandFrame(
  frameId: string | undefined,
  activeCommandId: string | null,
): boolean {
  if (activeCommandId && frameId && frameId !== activeCommandId) {
    return false;
  }
  return true;
}
