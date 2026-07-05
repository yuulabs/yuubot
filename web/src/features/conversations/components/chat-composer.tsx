import { useRef } from "react";
import { Paperclip, X } from "lucide-react";

import type { ActorSnapshot } from "@/shared/types/api";

export function ChatComposer({
  actors,
  selectedActor,
  actorLocked = false,
  text,
  attachments,
  onActorChange,
  onTextChange,
  onUpload,
  onRemoveAttachment,
  onSend,
  disabled = false,
  disabledReason = "",
  wsReady = false,
}: {
  actors: ActorSnapshot[];
  selectedActor: string;
  actorLocked?: boolean;
  text: string;
  attachments: string[];
  onActorChange: (actorId: string) => void;
  onTextChange: (text: string) => void;
  onUpload: (files: File[]) => void;
  onRemoveAttachment: (path: string) => void;
  onSend: () => void;
  disabled?: boolean;
  disabledReason?: string;
  wsReady?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const canSend = !disabled && Boolean(text.trim()) && wsReady;

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
          <input
            ref={fileRef}
            className="composer__file-input"
            type="file"
            multiple
            disabled={disabled}
            onChange={(event) => {
              onUpload(Array.from(event.target.files ?? []));
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
        </div>

        {attachments.length > 0 && (
          <div className="composer__attachments">
            {attachments.map((path) => (
              <span key={path} className="composer__attachment">
                <Paperclip size={14} />
                <span className="composer__attachment-name">{path.split("/").pop() ?? path}</span>
                <button
                  type="button"
                  className="composer__attachment-remove"
                  aria-label={`Remove ${path}`}
                  onClick={() => onRemoveAttachment(path)}
                >
                  <X size={14} />
                </button>
              </span>
            ))}
          </div>
        )}

        <textarea
          className="composer__input"
          rows={4}
          placeholder="Message the actor..."
          value={text}
          disabled={disabled}
          onChange={(event) => onTextChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              if (canSend) onSend();
            }
          }}
        />

        <div className="composer__footer">
          <span className={wsReady ? "composer__status is-ready" : "composer__status"}>
            {wsReady ? "Connected" : "Connecting..."}
          </span>
          <button
            type="button"
            className="composer__send composer__send--text"
            disabled={!canSend}
            aria-label="Send message"
            onClick={onSend}
          >
            Send
          </button>
        </div>
      </div>

      <div className="composer__hint">
        <span><b>Enter</b> for newline <span aria-hidden="true">·</span> <b>Ctrl+Enter</b> to send</span>
      </div>

      {disabledReason && <p className="chat__error">{disabledReason}</p>}
    </div>
  );
}
