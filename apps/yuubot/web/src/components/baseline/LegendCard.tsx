// LegendCard — demo fieldset.card: rounded white surface + colored dot legend.
// Renders as <fieldset><legend>… by default; pass as="div" for non-form use.
import type { ReactNode, ElementType } from "react";
import { cn } from "@/lib/utils";
import { Dot, type DotColor } from "./Dot";
interface LegendCardProps {
  legend?: ReactNode;
  dotColor?: DotColor;
  lead?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "fieldset" | "div";
}

export function LegendCard({
  legend,
  dotColor,
  lead,
  children,
  className,
  as = "fieldset",
}: LegendCardProps) {
  const Comp = (as === "div" ? "div" : "fieldset") as ElementType;
  return (
    <Comp className={cn("card", as === "fieldset" && "card--details", className)}>
      {legend != null && (
        <legend className="card__legend">
          {dotColor && <Dot color={dotColor} />}
          {legend}
        </legend>
      )}
      {lead != null && <p className="card__lead">{lead}</p>}
      {children}
    </Comp>
  );
}
