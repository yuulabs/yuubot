interface FormFieldProps {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
}

export function FormField({ label, htmlFor, children, className }: FormFieldProps) {
  return (
    <div className={`space-y-1 ${className ?? ""}`}>
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
    </div>
  );
}
