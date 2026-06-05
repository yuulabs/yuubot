import { Check, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

interface LaunchPathStep {
  done: boolean;
  label: string;
}

interface LaunchPathProps {
  steps: LaunchPathStep[];
  className?: string;
}

export function LaunchPath({ steps, className }: LaunchPathProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {steps.map((step) => (
        <div key={step.label} className="flex items-center gap-2 text-sm">
          {step.done ? (
            <Check size={16} className="text-green-500" />
          ) : (
            <Circle size={16} className="text-muted-foreground" />
          )}
          <span className={step.done ? "" : "text-muted-foreground"}>
            {step.label}
          </span>
        </div>
      ))}
    </div>
  );
}
