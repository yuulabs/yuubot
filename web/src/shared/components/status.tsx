import { CheckCircle2, CircleSlash } from "lucide-react";

export function Status({ enabled, label }: { enabled: boolean; label?: string }) {
  return (
    <span className={enabled ? "status-pill status-pill--ok" : "status-pill"}>
      {enabled ? <CheckCircle2 size={14} /> : <CircleSlash size={14} />}
      <span>{label ?? (enabled ? "enabled" : "disabled")}</span>
    </span>
  );
}
