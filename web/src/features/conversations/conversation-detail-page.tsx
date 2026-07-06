import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useTopbarActions } from "@/features/shell/app-layout";
import {
  deleteConversation,
  getConversationCosts,
  getConversationHistory,
  uploadActorFile,
  type WsContentItem,
} from "@/shared/lib/api";
import { ErrorState, LoadingState } from "@/shared/components";
import { useApiMutation, useBootstrap, useRefreshBootstrap } from "@/shared/hooks";
import type { HistoryItem } from "@/shared/types/api";
import { ChatComposer } from "./components/chat-composer";
import { ChatDebugDrawer } from "./components/chat-debug-drawer";
import { ChatLayout, ChatMain } from "./components/chat-layout";
import { ChatRail } from "./components/chat-rail";
import { ChatTopbar } from "./components/chat-topbar";
import { ChatTranscript } from "./components/chat-transcript";
import { useConversationSession } from "./hooks/use-conversation-session";
import { newConversationId, parsePendingSend } from "./lib/pending-send";
import { sumConversationCost } from "./lib/transcript";

export function ConversationDetailPage({
  conversationId,
  draftActorId = "",
}: {
  conversationId: string;
  draftActorId?: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const refreshBootstrap = useRefreshBootstrap();
  const { setActions } = useTopbarActions();
  const { data: bootstrap } = useBootstrap();
  const isDraft = conversationId === "new";
  const pendingSend = useRouterState({
    select: (state) => parsePendingSend(state.location.state),
  });
  const awaitingFirstSend = Boolean(pendingSend);
  const pendingSendConsumedRef = useRef(false);

  useEffect(() => {
    pendingSendConsumedRef.current = false;
  }, [conversationId]);
  const { data: history = [], error, isLoading } = useQuery({
    queryKey: ["conversation-history", conversationId],
    queryFn: () => getConversationHistory(conversationId),
    enabled: !isDraft && !awaitingFirstSend,
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
    enabled: !isDraft && !awaitingFirstSend,
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

  const handleHistoryAppend = useCallback((targetId: string, item: HistoryItem) => {
    const append = (current: HistoryItem[]) => (
      current.some((entry) => entry.seq === item.seq)
        ? current
        : [...current, item].sort((left, right) => left.seq - right.seq)
    );
    queryClient.setQueryData<HistoryItem[]>(
      ["conversation-history", targetId],
      (current = []) => append(current),
    );
  }, [queryClient]);

  const handleTurnComplete = useCallback(() => {
    void costs.refetch();
    refreshBootstrap();
  }, [costs, refreshBootstrap]);

  const session = useConversationSession({
    conversationId,
    history: isDraft ? [] : history,
    isDraft,
    development: Boolean(bootstrap?.development),
    onHistoryAppend: handleHistoryAppend,
    onTurnComplete: handleTurnComplete,
  });

  useEffect(() => {
    if (isDraft || pendingSendConsumedRef.current || !pendingSend || !session.wsReady) {
      return;
    }
    pendingSendConsumedRef.current = true;
    const sent = session.send(pendingSend.actorId, pendingSend.content, conversationId);
    if (!sent) {
      pendingSendConsumedRef.current = false;
      return;
    }
    void navigate({
      to: "/admin/conversations/$conversationId",
      params: { conversationId },
      replace: true,
      state: {},
    });
    void queryClient.prefetchQuery({
      queryKey: ["conversation-history", conversationId],
      queryFn: () => getConversationHistory(conversationId),
    });
    void queryClient.prefetchQuery({
      queryKey: ["conversation-costs", conversationId],
      queryFn: () => getConversationCosts(conversationId),
    });
  }, [conversationId, isDraft, navigate, pendingSend, queryClient, session.send, session.wsReady]);

  const attachmentPaths = attachments.map((item) => item.path ?? "").filter(Boolean);
  const activeConversationId = session.activeConversationId || conversationId;
  const totalCost = sumConversationCost(costs.data?.items ?? []);
  const displayItems = session.displayItems;
  const hasAcceptedDraftState = !isDraft
    && (
      awaitingFirstSend
      || session.phase !== "idle"
      || session.liveBlocks.length > 0
      || session.displayItems.length > 0
    );

  const interruptTarget = session.activeConversationId || (!isDraft ? conversationId : "");
  const conversationTopbarActions = useMemo(() => (
    <ChatTopbar
      actorId={selectedActor}
      historyOpen={historyOpen}
      debugOpen={debugOpen}
      showDebugToggle={Boolean(bootstrap?.development)}
      onToggleHistory={() => setHistoryOpen((value) => !value)}
      onToggleDebug={() => setDebugOpen((value) => !value)}
    />
  ), [
    bootstrap?.development,
    debugOpen,
    historyOpen,
    selectedActor,
  ]);

  useEffect(() => {
    setActions(conversationTopbarActions);
    return () => setActions(null);
  }, [conversationTopbarActions, setActions]);

  if (!isDraft && isLoading && !hasAcceptedDraftState) return <LoadingState />;
  if (!isDraft && error && !awaitingFirstSend) return <ErrorState error={error} />;

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
            waitingForResponse={session.waitingForResponse || awaitingFirstSend}
          />
          <ChatComposer
            actors={actors}
            selectedActor={selectedActor}
            actorLocked={isDraft}
            newConversationActorId={selectedActor}
            text={text}
            attachments={attachmentPaths}
            onActorChange={setActorId}
            onTextChange={setText}
            onUpload={(files) => upload.mutate(files)}
            onRemoveAttachment={(path) => {
              setAttachments((current) => current.filter((item) => item.path !== path));
            }}
            onSend={send}
            onInterrupt={() => session.interrupt(interruptTarget)}
            phase={session.phase}
            totalCost={totalCost}
            canInterrupt={Boolean(interruptTarget)}
            disabled={Boolean(disabledReason) || upload.isPending}
            disabledReason={disabledReason}
            wsReady={isDraft || session.wsReady}
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
    if (!selectedActor || disabledReason) return false;
    const content: WsContentItem[] = [];
    if (text.trim()) {
      content.push({ kind: "text", text: text.trim() });
    }
    content.push(...attachments);
    if (!content.length) return false;

    if (isDraft) {
      const id = newConversationId();
      void navigate({
        to: "/admin/conversations/$conversationId",
        params: { conversationId: id },
        state: { pendingSend: { actorId: selectedActor, content } },
        replace: true,
      });
      setText("");
      setAttachments([]);
      return true;
    }

    const sent = session.send(selectedActor, content, conversationId);
    if (!sent) return false;
    setText("");
    setAttachments([]);
    return true;
  }
}
