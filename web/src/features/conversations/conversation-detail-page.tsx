import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useTopbarActions } from "@/features/shell/app-layout";
import {
  createConversation,
  deleteConversation,
  getConversationUsage,
  uploadActorFile,
  type WsContentItem,
} from "@/shared/lib/api";
import { ErrorState, LoadingState } from "@/shared/components";
import { conversationsQueryKey, useApiMutation, useBootstrap, useConversations } from "@/shared/hooks";
import { describeConversationError } from "@/shared/lib/api-errors";
import { segmentsToText, type ComposerSegment } from "@/shared/lib/workspace-ref";
import { ChatComposer } from "./components/chat-composer";
import { ChatDebugDrawer } from "./components/chat-debug-drawer";
import { ChatLayout, ChatMain } from "./components/chat-layout";
import { ChatRail } from "./components/chat-rail";
import { ChatTopbar } from "./components/chat-topbar";
import { ChatTranscript } from "./components/chat-transcript";
import { useConversationSession } from "./hooks/use-conversation-session";
import { parsePendingSend } from "./lib/pending-send";
import { sumConversationTokens } from "./lib/transcript";

export function ConversationDetailPage({
  conversationId,
  draftActorId = "",
  draftPrompt = "",
}: {
  conversationId: string;
  draftActorId?: string;
  draftPrompt?: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { setActions } = useTopbarActions();
  const { data: bootstrap } = useBootstrap();
  const conversations = useConversations();
  const isDraft = conversationId === "new";
  const pendingSend = useRouterState({
    select: (state) => parsePendingSend(state.location.state),
  });
  const awaitingFirstSend = Boolean(pendingSend);
  const pendingSendConsumedRef = useRef(false);

  useEffect(() => {
    pendingSendConsumedRef.current = false;
  }, [conversationId]);
  const [actorId, setActorId] = useState(draftActorId);
  const [segments, setSegments] = useState<ComposerSegment[]>([]);
  const [draftText, setDraftText] = useState(draftPrompt);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);

  const actors = bootstrap?.actors ?? [];
  const durableSummary = conversations.data?.find((item) => item.id === conversationId);
  const selectedActor = useMemo(
    () => draftActorId || actorId || durableSummary?.actor_id || actors[0]?.id || "",
    [actorId, actors, draftActorId, durableSummary?.actor_id],
  );
  const relatedConversations = (conversations.data ?? []).filter(
    (item) => !selectedActor || item.actor_id === selectedActor,
  );
  const remove = useApiMutation((id: string) => deleteConversation(id));
  const selectedActorSnapshot = actors.find((actor) => actor.id === selectedActor);
  const disabledReason = !selectedActorSnapshot
    ? "Select an actor before sending."
    : !selectedActorSnapshot.enabled
      ? "Actor is disabled."
      : "";

  useEffect(() => {
    setSegments([]);
    setDraftText(isDraft ? draftPrompt : "");
    setHistoryOpen(false);
  }, [conversationId, draftPrompt, isDraft, selectedActor]);

  const usage = useQuery({
    queryKey: ["conversation-usage", conversationId],
    queryFn: () => getConversationUsage(conversationId),
    enabled: !isDraft && !awaitingFirstSend,
  });

  const upload = useMutation({
    mutationFn: (files: File[]) => uploadActorFile(selectedActor, files),
  });

  const handleTurnComplete = useCallback(() => {
    void usage.refetch();
    void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
  }, [usage, queryClient]);

  const session = useConversationSession({
    conversationId,
    isDraft,
    development: Boolean(bootstrap?.development),
    onTurnComplete: handleTurnComplete,
  });

  useEffect(() => {
    const retry = session.retrySend;
    if (!retry) return;
    const restored = retry.content.reduce<ComposerSegment[]>((items, item) => {
      if (item.kind === "text" && item.text) {
        items.push({ kind: "text", value: item.text });
      }
      if (item.kind === "file" && item.path) {
        items.push({
          kind: "file",
          path: item.path,
          mime: item.mime,
          meta: item.meta,
        });
      }
      return items;
    }, []);
    setSegments(restored);
    setDraftText("");
    session.clearRetrySend();
  }, [session.retrySend, session.clearRetrySend]);

  const persistedError = describeConversationError(durableSummary?.last_error);
  const showPersistedError = Boolean(persistedError) && !session.error;

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
      queryKey: ["conversation-usage", conversationId],
      queryFn: () => getConversationUsage(conversationId),
    });
  }, [conversationId, isDraft, navigate, pendingSend, queryClient, session.send, session.wsReady]);

  const activeConversationId = session.activeConversationId || conversationId;
  const sendText = buildSendText(segments, draftText);
  const hasComposerContent = sendText.length > 0;
  const totalTokens = sumConversationTokens(usage.data?.items ?? []);
  const usageItems = usage.data?.items ?? [];
  const latestUsage = usageItems.length ? usageItems[usageItems.length - 1].usage : undefined;
  const latestInputTokens = durableSummary?.last_input_tokens ?? numericUsage(latestUsage?.input_tokens);
  const maxContextTokens = null;
  const contextUsageLabel = `${formatTokens(latestInputTokens ?? 0)} / ${maxContextTokens ? formatTokens(maxContextTokens) : "unknown"}`;
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

  if (!isDraft && conversations.isLoading && !hasAcceptedDraftState) return <LoadingState />;
  if (!isDraft && conversations.error && !awaitingFirstSend) return <ErrorState error={conversations.error} />;

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
      onRailOpenChange={setHistoryOpen}
      main={
        <ChatMain>
          {selectedActorSnapshot?.loaded_skills_warning && <p className="chat__error">当前加载 {selectedActorSnapshot.loaded_skill_count} 个 skills，建议不超过 {selectedActorSnapshot.max_loaded_skills_warning} 个。请前往 Actor 页面管理。</p>}
          {session.error && <p className="chat__error">{session.error}</p>}
          {showPersistedError && (
            <p className="chat__error">
              {persistedError}
              {durableSummary?.status ? ` (status: ${durableSummary.status})` : ""}
            </p>
          )}
          <ChatTranscript
            actorId={selectedActor}
            items={displayItems}
            phase={session.phase}
            scrollResetKey={activeConversationId}
            waitingForResponse={session.waitingForResponse || awaitingFirstSend}
            onAnswerQuestion={session.answerQuestion}
            hasOlder={session.hasOlder}
            loadingOlder={session.loadingOlder}
            onLoadOlder={session.loadOlder}
          />
          <ChatComposer
            actors={actors}
            selectedActor={selectedActor}
            actorLocked={isDraft}
            newConversationActorId={selectedActor}
            segments={segments}
            draftText={draftText}
            hasContent={hasComposerContent}
            onActorChange={setActorId}
            onDraftTextChange={setDraftText}
            onUploadAtCursor={uploadAtCursor}
            onRemoveSegment={(index) => {
              setSegments((current) => current.filter((_, itemIndex) => itemIndex !== index));
            }}
            onSend={send}
            onInterrupt={() => session.interrupt(interruptTarget)}
            phase={session.phase}
            totalTokens={totalTokens}
            contextUsageLabel={contextUsageLabel}
            canInterrupt={Boolean(interruptTarget)}
            disabled={Boolean(disabledReason) || upload.isPending || session.awaitingInput}
            disabledReason={session.awaitingInput ? "Answer the pending questions to continue." : disabledReason}
            wsReady={isDraft || session.wsReady}
            wsConnectionState={session.wsConnectionState}
          />
          {upload.error && (
            <p className="chat__error">
              {upload.error instanceof Error ? upload.error.message : String(upload.error)}
            </p>
          )}
          {bootstrap?.development && debugOpen && (
            <ChatDebugDrawer
              events={session.events}
              usageJson={JSON.stringify(usage.data?.items ?? [], null, 2)}
            />
          )}
        </ChatMain>
      }
    />
  );

  function send() {
    if (!selectedActor || disabledReason) return false;
    const content = buildSendContent(segments, draftText);
    if (!content.length) return false;

    if (isDraft) {
      void createConversation(selectedActor).then(({ conversation_id: id }) => {
        void queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
        void navigate({
          to: "/admin/conversations/$conversationId",
          params: { conversationId: id },
          state: { pendingSend: { actorId: selectedActor, content } },
          replace: true,
        });
      });
      clearComposer();
      return true;
    }

    const sent = session.send(selectedActor, content, conversationId);
    if (!sent) return false;
    clearComposer();
    return true;
  }

  function uploadAtCursor(files: File[], cursor: number) {
    if (!files.length) return;
    const before = draftText.slice(0, cursor);
    const after = draftText.slice(cursor);
    setSegments((current) => before ? [...current, { kind: "text", value: before }] : current);
    setDraftText(after);
    upload.mutate(files, {
      onSuccess: (result) => {
        const uploaded = result.files
          .map((item): ComposerSegment | null => {
            const path = String(item.path ?? "");
            if (!path) return null;
            return {
              kind: "file",
              path,
              mime: typeof item.mime === "string" ? item.mime : undefined,
              meta: typeof item.meta === "object" && item.meta ? (item.meta as Record<string, unknown>) : undefined,
            };
          })
          .filter((item): item is ComposerSegment => item !== null);
        if (uploaded.length) {
          setSegments((current) => [...current, ...uploaded]);
        }
      },
    });
  }

  function clearComposer() {
    setSegments([]);
    setDraftText("");
  }
}

function buildSendText(segments: ComposerSegment[], draftText: string): string {
  const all = draftText ? [...segments, { kind: "text", value: draftText } satisfies ComposerSegment] : segments;
  return segmentsToText(all).trim();
}

function buildSendContent(segments: ComposerSegment[], draftText: string): WsContentItem[] {
  const content: WsContentItem[] = [];
  for (const segment of segments) {
    if (segment.kind === "text") {
      if (segment.value.trim()) {
        content.push({ kind: "text", text: segment.value });
      }
      continue;
    }
    content.push({ kind: "file", path: segment.path, mime: segment.mime, meta: segment.meta });
  }
  if (draftText.trim()) {
    content.push({ kind: "text", text: draftText });
  }
  return content;
}

function numericUsage(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function formatTokens(value: number): string {
  return Math.trunc(value).toLocaleString();
}
