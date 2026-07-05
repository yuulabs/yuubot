import type { ReactNode } from "react";

export function Page({ title, sub, actions, children }: { title: string; sub?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="view">
      <div className="page-head">
        <div>
          <h1 className="page-title">{title}</h1>
          {sub && <p className="page-sub">{sub}</p>}
        </div>
        {actions && <div className="page-head__actions">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

export function LoadingState() {
  return <div className="empty">Loading...</div>;
}

export function ErrorState({ error }: { error: unknown }) {
  return <div className="empty">Error: {error instanceof Error ? error.message : String(error)}</div>;
}

export function EmptyState({ children = "No records yet." }: { children?: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Panel({ children }: { children: ReactNode }) {
  return <div className="section-card">{children}</div>;
}

export function RecordTable({ children }: { children: ReactNode }) {
  return <div className="data-table">{children}</div>;
}
