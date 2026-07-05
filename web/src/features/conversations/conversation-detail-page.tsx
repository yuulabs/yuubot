import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useTopbarActions } from "@/features/shell/app-layout";
import {
  deleteConversation,
  getConversationCosts,
  getConversationHistory,
  uploadActorFile,
  type WsContentItem,
} from "@/shared/lib/api";
import { ErrorState, LoadingState } from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";
import { ChatComposer } from "./components/chat-composer";
import { ChatDebugDrawer } from "./components/chat-debug-drawer";
import { ChatLayout, ChatMain } from "./components/chat-layout";
import { ChatRail } from "./components/chat-rail";
import { ChatTopbar } from "./components/chat-topbar";
import { ChatTranscript } from "./components/chat-transcript";
import { useConversationSession } from "./hooks/use-conversation-session";
import { buildDisplayItems } from "./lib/conversation-transcript";
import { sumConversationCost } from "./lib/transcript";

export function ConversationDetailPage({ conversationId }: { conversationId: string }) {
  const navigate = useNavigate();
  const { setActions } = useTopbarActions();
  const { data: bootstrap } = useBootstrap();
  const draftActorId = conversationId.startsWith("actor-") ? conversationId.slice("actor-".length) : "";
  const isDraft = Boolean(draftActorId);
  const { data: history = [], error, isLoading } = useQuery({
    queryKey: ["conversation-history", conversationId],
    queryFn: () => getConversationHistory(conversationId),
    enabled: !isDraft,
  });
  const [actorId, setActorId] = useState(draftActorId);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<WsContentItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);

  const actors = bootstrap?.actors ?? [];
  const providers = bootstrap?.providers ?? [];
  const durableSummary = bootstrap?.conversations.find((item) => item.id === conversationId);
  const selectedActor = useMemo(
    () => draftActorId || actorId || durableSummary?.actor_id || actors[0]?.id || "",
    [actorId, actors, draftActorId, durableSummary?.actor_id],
  );
  const relatedConversations = (bootstrap?.conversations ?? []).filter(
    (item) => !selectedActor || item.actor_id === selectedActor,
  );
  const remove = useApiMutation((id: string) => deleteConversation(id));
  const selectedActorSnapshot = actors.find((actor) => actor.id === selectedActor);
  const selectedProvider = providers.find((provider) => provider.id === selectedActorSnapshot?.provider);
  const disabledReason = !selectedActorSnapshot
    ? "Select an actor before sending."
    : !selectedActorSnapshot.enabled
      ? "Actor is disabled."
      : !selectedProvider?.configured
        ? "Actor provider is not configured."
        : "";

  const costs = useQuery({
    queryKey: ["conversation-costs", conversationId],
    queryFn: () => getConversationCosts(conversationId),
    enabled: !isDraft,
  });

  const upload = useMutation({
    mutationFn: (files: File[]) => uploadActorFile(selectedActor, files),
    onSuccess: (result) => {
      setAttachments((current) => [
        ...current,
        ...result.files
          .map((item) => ({
            kind: String(item.kind ?? "file"),
            path: String(item.path ?? ""),
            mime: typeof item.mime === "string" ? item.mime : undefined,
            meta: typeof item.meta === "object" && item.meta ? (item.meta as Record<string, unknown>) : undefined,
          }))
          .filter((item) => item.path),
      ]);
    },
  });

  const handleConversationAccepted = useCallback(
    (acceptedId: string) => {
      void navigate({
        to: "/admin/conversations/$conversationId",
        params: { conversationId: acceptedId },
        replace: true,
      });
    },
    [navigate],
  );

  const handleStreamStop = useCallback(() => {
    void costs.refetch();
  }, [costs]);

  const session = useConversationSession({
    conversationId,
    isDraft,
    development: Boolean(bootstrap?.development),
    onConversationAccepted: handleConversationAccepted,
    onStreamStop: handleStreamStop,
  });

  const attachmentPaths = attachments.map((item) => item.path ?? "").filter(Boolean);
  const activeConversationId = session.activeConversationId || conversationId;
  const totalCost = sumConversationCost(costs.data?.items ?? []);
  const displayItems = buildDisplayItems({
    history,
    liveBlocks: session.liveBlocks,
    optimisticUserText: session.optimisticUserText,
    phase: session.phase,
    turnKey: session.turnKey,
  });

  const interruptTarget = session.activeConversationId || (!isDraft ? conversationId : "");
  const conversationTopbarActions = useMemo(() => (
    <ChatTopbar
      phase={session.phase}
      totalCost={totalCost}
      canInterrupt={Boolean(interruptTarget)}
      historyOpen={historyOpen}
      debugOpen={debugOpen}
      showDebugToggle={Boolean(bootstrap?.development)}
      onToggleHistory={() => setHistoryOpen((value) => !value)}
      onToggleDebug={() => setDebugOpen((value) => !value)}
      onInterrupt={() => session.interrupt(interruptTarget)}
    />
  ), [
    bootstrap?.development,
    debugOpen,
    historyOpen,
    interruptTarget,
    session.interrupt,
    session.phase,
    totalCost,
  ]);

  useEffect(() => {
    setActions(conversationTopbarActions);
    return () => setActions(null);
  }, [conversationTopbarActions, setActions]);

  if (!isDraft && isLoading) return <LoadingState />;
  if (!isDraft && error) return <ErrorState error={error} />;

  return (
    <ChatLayout
      rail={
        <ChatRail
          actorId={selectedActor}
          actorName={selectedActorSnapshot?.name || selectedActor}
          conversations={relatedConversations}
          activeConversationId={activeConversationId}
          onDelete={(id) => {
            remove.mutate(id);
            if (id === conversationId) {
              void navigate({ to: "/admin/conversations" });
            }
          }}
        />
      }
      railOpen={historyOpen}
      main={
        <ChatMain>
          {session.error && <p className="chat__error">{session.error}</p>}
          <ChatTranscript
            items={displayItems}
            phase={session.phase}
            waitingForResponse={session.waitingForResponse}
          />
          <ChatComposer
            actors={actors}
            selectedActor={selectedActor}
            actorLocked={isDraft}
            text={text}
            attachments={attachmentPaths}
            onActorChange={setActorId}
            onTextChange={setText}
            onUpload={(files) => upload.mutate(files)}
            onRemoveAttachment={(path) => {
              setAttachments((current) => current.filter((item) => item.path !== path));
            }}
            onSend={send}
            disabled={Boolean(disabledReason) || upload.isPending}
            disabledReason={disabledReason}
            wsReady={session.wsReady}
          />
          {upload.error && (
            <p className="chat__error">
              {upload.error instanceof Error ? upload.error.message : String(upload.error)}
            </p>
          )}
          {bootstrap?.development && debugOpen && (
            <ChatDebugDrawer
              events={session.events}
              costsJson={JSON.stringify(costs.data?.items ?? [], null, 2)}
            />
          )}
        </ChatMain>
      }
    />
  );

  function send() {
    if (!selectedActor || disabledReason) return;
    const content: WsContentItem[] = [];
    if (text.trim()) {
      content.push({ kind: "text", text: text.trim() });
    }
    content.push(...attachments);
    if (!content.length) return;
    const sent = session.send(
      selectedActor,
      content,
      isDraft ? undefined : conversationId,
    );
    if (!sent) return;
    setText("");
    setAttachments([]);
  }
}
