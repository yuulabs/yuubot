import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/shared/lib/utils";

export interface ResourceListColumn<T> {
  key: string;
  label: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
  headerClassName?: string;
}

export function ResourceList<T>({
  rows,
  columns,
  getRowId,
  emptyLabel = "No records.",
  className,
}: {
  rows: T[];
  columns: ResourceListColumn<T>[];
  getRowId: (row: T) => string;
  emptyLabel?: ReactNode;
  className?: string;
}) {
  if (!rows.length) {
    return <div className="resource-list-empty">{emptyLabel}</div>;
  }

  return (
    <div className={cn("resource-list", className)}>
      <table className="resource-list__table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.headerClassName}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={getRowId(row)}>
              {columns.map((column) => (
                <td key={column.key} className={column.className}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResourceListPrimary({
  title,
  subtitle,
  meta,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="resource-list-primary">
      <div className="resource-list-primary__title">{title}</div>
      {subtitle && <div className="resource-list-primary__subtitle">{subtitle}</div>}
      {meta && <div className="resource-list-primary__meta">{meta}</div>}
    </div>
  );
}

export interface DenseMetaItem {
  label: string;
  value: ReactNode;
  tone?: "default" | "ok" | "warning" | "danger" | "muted";
}

export function DenseMeta({ items, className }: { items: DenseMetaItem[]; className?: string }) {
  return (
    <dl className={cn("dense-meta", className)}>
      {items.map((item) => (
        <div key={item.label} className={cn("dense-meta__item", item.tone && `dense-meta__item--${item.tone}`)}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DenseSection({
  title,
  description,
  actions,
  children,
  className,
  ...props
}: HTMLAttributes<HTMLElement> & {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className={cn("dense-section", className)} {...props}>
      {(title || description || actions) && (
        <div className="dense-section__head">
          <div className="dense-section__title-block">
            {title && <h2 className="dense-section__title">{title}</h2>}
            {description && <p className="dense-section__description">{description}</p>}
          </div>
          {actions && <div className="dense-section__actions">{actions}</div>}
        </div>
      )}
      <div className="dense-section__body">{children}</div>
    </section>
  );
}
