// StatusPill — demo .pill, variant picks the colorway; optional leading dot.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export type StatusVariant = "draft" | "paused" | "running" | "connected" | "default";

interface StatusPillProps {
  variant?: StatusVariant;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}

export function StatusPill({ variant = "default", children, dot = true, className }: StatusPillProps) {
  return (
    <span className={cn("pill", `pill--${variant}`, !dot && "pill--no-dot", className)}>
      {children}
    </span>
  );
}
