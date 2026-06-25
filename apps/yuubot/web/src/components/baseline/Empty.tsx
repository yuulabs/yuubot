// Empty — three-part empty state: illustration chip + h3 + p + optional CTA.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface EmptyProps {
  illustration?: string;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function Empty({ illustration, title, description, action, className }: EmptyProps) {
  return (
    <div className={cn("empty", className)}>
      {illustration && <div className="empty__ill">{illustration}</div>}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
