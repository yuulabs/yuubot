// Dot — the rotated-square legend atom (demo .dot--*).
// Composed inside <LegendCard legend={<>...<Dot color="indigo" /> Basic</>}>.
import { cn } from "@/lib/utils";

export type DotColor = "indigo" | "amber" | "green" | "slate";

interface DotProps {
  color?: DotColor;
  className?: string;
}

export function Dot({ color = "indigo", className }: DotProps) {
  return (
    <span
      className={cn("dot", `dot--${color}`, className)}
      aria-hidden="true"
    />
  );
}
