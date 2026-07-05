import { Button } from "@/components/ui/button";
import type { ActorSnapshot } from "@/shared/types/api";

export function ConversationComposer({
  actors,
  selectedActor,
  actorLocked = false,
  text,
  attachments,
  onActorChange,
  onTextChange,
  onUpload,
  onSend,
  onInterrupt,
  disabled = false,
  disabledReason = "",
}: {
  actors: ActorSnapshot[];
  selectedActor: string;
  actorLocked?: boolean;
  text: string;
  attachments: string[];
  onActorChange: (actorId: string) => void;
  onTextChange: (text: string) => void;
  onUpload: (files: File[]) => void;
  onSend: () => void;
  onInterrupt: () => void;
  disabled?: boolean;
  disabledReason?: string;
}) {
  return (
    <div className="grid gap-2">
      <div className="grid gap-2 md:grid-cols-[180px_1fr_auto_auto]">
        <select className="input" value={selectedActor} disabled={actorLocked || disabled} onChange={(event) => onActorChange(event.target.value)}>
          {actors.map((actor) => <option key={actor.id} value={actor.id}>{actor.name || actor.id}</option>)}
        </select>
        <textarea
          className="textarea"
          rows={3}
          value={text}
          onChange={(event) => onTextChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              onSend();
            }
          }}
        />
        <Button disabled={disabled} onClick={onSend}>Send</Button>
        <Button variant="outline" onClick={onInterrupt}>Interrupt</Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input className="input" type="file" multiple disabled={disabled} onChange={(event) => onUpload(Array.from(event.target.files ?? []))} />
        {attachments.map((path) => <span key={path} className="page-sub">{path}</span>)}
      </div>
      {disabledReason && <p className="text-sm text-destructive">{disabledReason}</p>}
    </div>
  );
}
