// RailCard — detail/editor rail card with optional lead, hint, danger variant.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface RailCardProps {
  title: string;
  lead?: string;
  hint?: string;
  danger?: boolean;
  children: ReactNode;
  className?: string;
}

export function RailCard({ title, lead, hint, danger, children, className }: RailCardProps) {
  return (
    <div className={cn("rail-card", danger && "rail-card--danger", className)}>
      <h4 className="rail-card__title">{title}</h4>
      {lead && <p className="rail-card__lead">{lead}</p>}
      {children}
      {hint && <p className="rail-card__hint">{hint}</p>}
    </div>
  );
}
