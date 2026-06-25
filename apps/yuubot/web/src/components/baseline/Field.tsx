// Field — label + hint + control wrapper (demo .field / .field--inline).
// The control (input/select/textarea) is passed as children; Field does not
// own the input primitive so callers may pick @/components/ui/* freely.
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface FieldProps {
  label: string;
  required?: boolean;
  hint?: string;
  inline?: boolean;
  children: ReactNode;
  className?: string;
}

export function Field({ label, required, hint, inline, children, className }: FieldProps) {
  return (
    <label className={cn("field", inline && "field--inline", className)}>
      <span className="field__label">
        {label}
        {required && <span className="field__required" aria-hidden="true">*</span>}
      </span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  );
}
