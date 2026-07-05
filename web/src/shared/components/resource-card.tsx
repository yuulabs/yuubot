import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/shared/lib/utils";

export type ResourceCardVariant =
  | "actor"
  | "provider"
  | "integration"
  | "route"
  | "conversation"
  | "task"
  | "neutral";

export interface ResourceMetaItem {
  label: string;
  value: ReactNode;
  tone?: "default" | "ok" | "warning" | "danger" | "muted";
}

export function ResourceCardGrid({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("resource-card-grid", className)} {...props} />;
}

export function ResourceCard({
  variant = "neutral",
  title,
  subtitle,
  label,
  status,
  actions,
  children,
  footer,
  selected = false,
  className,
  ...props
}: Omit<HTMLAttributes<HTMLElement>, "title"> & {
  variant?: ResourceCardVariant;
  title: ReactNode;
  subtitle?: ReactNode;
  label?: ReactNode;
  status?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  selected?: boolean;
}) {
  return (
    <article
      className={cn("resource-card", `resource-card--${variant}`, selected && "is-selected", className)}
      {...props}
    >
      <div className="resource-card__chrome" aria-hidden="true" />
      <ResourceCardHeader title={title} subtitle={subtitle} label={label} status={status} actions={actions} />
      {children && <div className="resource-card__body">{children}</div>}
      {footer && <div className="resource-card__footer">{footer}</div>}
    </article>
  );
}

export function ResourceCardHeader({
  title,
  subtitle,
  label,
  status,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  label?: ReactNode;
  status?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="resource-card__head">
      <div className="resource-card__title-block">
        {label && <div className="resource-card__label">{label}</div>}
        <div className="resource-card__title-row">
          <h2 className="resource-card__title">{title}</h2>
          {status && <div className="resource-card__status">{status}</div>}
        </div>
        {subtitle && <p className="resource-card__subtitle">{subtitle}</p>}
      </div>
      {actions && <ResourceActions>{actions}</ResourceActions>}
    </div>
  );
}

export function ResourceMeta({ items }: { items: ResourceMetaItem[] }) {
  return (
    <dl className="resource-meta">
      {items.map((item) => (
        <div key={item.label} className={cn("resource-meta__item", item.tone && `resource-meta__item--${item.tone}`)}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ResourceMetric({ label, value, hint }: { label: ReactNode; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="resource-metric">
      <span className="resource-metric__value">{value}</span>
      <span className="resource-metric__label">{label}</span>
      {hint && <span className="resource-metric__hint">{hint}</span>}
    </div>
  );
}

export function ResourceActions({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("resource-actions", className)} {...props} />;
}
