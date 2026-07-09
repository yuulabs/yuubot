import type { RefObject } from "react";

import { WorkspaceRefView } from "@/components/conversation/workspace-ref-view";
import type { ComposerSegment } from "@/shared/lib/workspace-ref";

export function ComposerInlineEditor({
  actorId,
  segments,
  draftText,
  disabled,
  textareaRef,
  onDraftTextChange,
  onUploadAtCursor,
  onRemoveSegment,
  onSendShortcut,
}: {
  actorId: string;
  segments: ComposerSegment[];
  draftText: string;
  disabled: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onDraftTextChange: (text: string) => void;
  onUploadAtCursor: (files: File[], cursor: number) => void;
  onRemoveSegment: (index: number) => void;
  onSendShortcut: () => void;
}) {
  const uploadAtSelection = (files: FileList | File[]) => {
    const selected = Array.from(files);
    if (!selected.length) return;
    onUploadAtCursor(selected, textareaRef.current?.selectionStart ?? draftText.length);
  };

  return (
    <div
      className="composer__inline-body"
      onDrop={(event) => {
        if (!event.dataTransfer.files.length || disabled) return;
        event.preventDefault();
        uploadAtSelection(event.dataTransfer.files);
      }}
      onDragOver={(event) => {
        if (disabled) return;
        event.preventDefault();
      }}
    >
      {segments.map((segment, index) => segment.kind === "text" ? (
        <span key={index} className="composer__inline-text">{segment.value}</span>
      ) : (
        <WorkspaceRefView
          key={index}
          actorId={actorId}
          path={segment.path}
          mime={segment.mime}
          onRemove={() => onRemoveSegment(index)}
        />
      ))}
      <textarea
        ref={textareaRef}
        className="composer__input composer__input--inline"
        rows={2}
        placeholder="Message the actor..."
        value={draftText}
        disabled={disabled}
        onChange={(event) => onDraftTextChange(event.target.value)}
        onPaste={(event) => {
          if (!event.clipboardData.files.length || disabled) return;
          event.preventDefault();
          uploadAtSelection(event.clipboardData.files);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            onSendShortcut();
          }
        }}
      />
    </div>
  );
}
