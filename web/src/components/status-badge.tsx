import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface StatusBadgeProps {
  enabled: boolean;
  className?: string;
}

export function StatusBadge({ enabled, className }: StatusBadgeProps) {
  return (
    <Badge variant={enabled ? "default" : "secondary"} className={cn("font-normal", className)}>
      {enabled ? "Enabled" : "Disabled"}
    </Badge>
  );
}
