interface EmptyStateProps {
  label: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  className?: string;
}

export function EmptyState({ label, icon: Icon, className }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 text-muted-foreground ${className ?? ""}`}>
      {Icon && <Icon size={32} className="mb-2" />}
      <p className="text-sm">{label}</p>
    </div>
  );
}
