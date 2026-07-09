import { FileText, X } from "lucide-react";

import { getActorFileUrl } from "@/shared/lib/api";

export function WorkspaceRefView({
  actorId,
  path,
  mime = "",
  onRemove,
}: {
  actorId: string;
  path: string;
  mime?: string;
  onRemove?: () => void;
}) {
  const url = actorId ? getActorFileUrl(actorId, path) : "";
  const name = path.split("/").pop() || path;
  const image = isImageWorkspaceRef(path, mime);

  return (
    <span className={image ? "workspace-ref workspace-ref--image" : "workspace-ref workspace-ref--file"}>
      {image && url ? (
        <a className="workspace-ref__image-link" href={url} target="_blank" rel="noopener noreferrer" title={path}>
          <img className="workspace-ref__image" src={url} alt={name} loading="lazy" />
        </a>
      ) : (
        <a
          className="workspace-ref__file-link"
          href={url || undefined}
          target="_blank"
          rel="noopener noreferrer"
          title={path}
        >
          <FileText size={15} />
          <span className="workspace-ref__file-name">{name}</span>
        </a>
      )}
      {onRemove && (
        <button type="button" className="workspace-ref__remove" aria-label={`Remove ${name}`} onClick={onRemove}>
          <X size={13} />
        </button>
      )}
    </span>
  );
}

export function isImageWorkspaceRef(path: string, mime = ""): boolean {
  if (mime.startsWith("image/")) return true;
  return /\.(?:avif|gif|jpe?g|png|webp)$/i.test(path);
}
