import { useRef } from "react";
import { MessageSquarePlus, Paperclip, SquareStop } from "lucide-react";

import { UsageBadge } from "@/components/conversation/usage-badge";
import type { ComposerSegment } from "@/shared/lib/workspace-ref";
import type { ActorSnapshot } from "@/shared/types/api";

import type { ConversationPhase, WsConnectionState } from "../hooks/use-conversation-session";
import { ComposerInlineEditor } from "./composer-inline-editor";
import { NewConversationLink } from "./new-conversation-link";
import { TurnPill } from "./turn-pill";

export function ChatComposer({
  actors,
  selectedActor,
  actorLocked = false,
  segments,
  draftText,
  hasContent,
  onActorChange,
  onDraftTextChange,
  onUploadAtCursor,
  onRemoveSegment,
  onSend,
  onInterrupt,
  phase = "idle",
  totalTokens = 0,
  contextUsageLabel = "",
  canInterrupt = false,
  disabled = false,
  disabledReason = "",
  wsReady = false,
  wsConnectionState = "connecting",
  newConversationActorId = "",
}: {
  actors: ActorSnapshot[];
  selectedActor: string;
  actorLocked?: boolean;
  newConversationActorId?: string;
  segments: ComposerSegment[];
  draftText: string;
  hasContent: boolean;
  onActorChange: (actorId: string) => void;
  onDraftTextChange: (text: string) => void;
  onUploadAtCursor: (files: File[], cursor: number) => void;
  onRemoveSegment: (index: number) => void;
  onSend: () => boolean;
  onInterrupt: () => void;
  phase?: ConversationPhase;
  totalTokens?: number;
  contextUsageLabel?: string;
  canInterrupt?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  wsReady?: boolean;
  wsConnectionState?: WsConnectionState;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isActive = phase === "sending" || phase === "streaming";
  const canSend = !disabled && hasContent && wsReady && !isActive;
  const connectionLabel = wsConnectionState === "connected"
    ? "Connected"
    : wsConnectionState === "reconnecting"
      ? "Reconnecting..."
      : "Connecting...";
  const sendAndRefocus = () => {
    if (!canSend) return;
    if (!onSend()) return;
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  return (
    <div className="chat__composer">
      <div className="composer__panel">
        <div className="composer__toolbar">
          {!actorLocked && (
            <select
              className="input composer__actor"
              value={selectedActor}
              disabled={disabled}
              onChange={(event) => onActorChange(event.target.value)}
              aria-label="Actor"
            >
              {actors.map((actor) => (
                <option key={actor.id} value={actor.id}>{actor.name || actor.id}</option>
              ))}
            </select>
          )}
          <NewConversationLink actorId={newConversationActorId} className="composer__icon-btn">
            <MessageSquarePlus size={16} />
          </NewConversationLink>
          <input
            ref={fileRef}
            className="composer__file-input"
            type="file"
            multiple
            disabled={disabled}
            onChange={(event) => {
              onUploadAtCursor(Array.from(event.target.files ?? []), textareaRef.current?.selectionStart ?? draftText.length);
              event.target.value = "";
            }}
          />
          <button
            type="button"
            className="composer__icon-btn"
            disabled={disabled}
            aria-label="Attach files"
            onClick={() => fileRef.current?.click()}
          >
            <Paperclip size={16} />
          </button>
          <div className="composer__toolbar-end">
            {contextUsageLabel && (
              <span className="text-sm text-muted-foreground tabular-nums" title="Latest input tokens / model max context tokens">
                {contextUsageLabel}
              </span>
            )}
            <TurnPill phase={phase} />
            <UsageBadge totalTokens={totalTokens} />
          </div>
        </div>

        <ComposerInlineEditor
          actorId={selectedActor}
          segments={segments}
          draftText={draftText}
          disabled={disabled}
          textareaRef={textareaRef}
          onDraftTextChange={onDraftTextChange}
          onUploadAtCursor={onUploadAtCursor}
          onRemoveSegment={onRemoveSegment}
          onSendShortcut={sendAndRefocus}
        />

        <div className="composer__footer">
          <span className={wsReady ? "composer__status is-ready" : "composer__status"}>
            {connectionLabel}
          </span>
          {isActive ? (
            <button
              type="button"
              className="composer__send composer__send--text composer__send--stop"
              disabled={!canInterrupt}
              aria-label="Stop generation"
              onClick={onInterrupt}
            >
              <SquareStop size={14} />
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="composer__send composer__send--text"
              disabled={!canSend}
              aria-label="Send message"
              onClick={sendAndRefocus}
            >
              Send
            </button>
          )}
        </div>
      </div>

      <div className="composer__hint">
        <span><b>Enter</b> for newline <span aria-hidden="true">·</span> <b>Ctrl+Enter</b> to send</span>
      </div>

      {disabledReason && <p className="chat__error">{disabledReason}</p>}
    </div>
  );
}
