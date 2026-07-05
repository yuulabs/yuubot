import type { ComponentType } from "react";

export function UsageTile({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <article className="monitor-stat">
      <div className="monitor-stat__icon">
        <Icon size={18} />
      </div>
      <div className="monitor-stat__body">
        <p className="monitor-stat__label">{label}</p>
        <p className="monitor-stat__value monitor-stat__value--mono">{value}</p>
        {sub && <p className="monitor-stat__sub">{sub}</p>}
      </div>
    </article>
  );
}

export function ChartEmpty({ message }: { message: string }) {
  return (
    <div className="monitor-chart-empty">
      {message}
    </div>
  );
}
