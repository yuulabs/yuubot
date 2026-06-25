// PageShell — resource-page outer shell.
// topbar (crumbs + actions) + page-head (title + sub) + children.
import type { ReactNode } from "react";

interface PageShellProps {
  crumbs?: string[];
  actions?: ReactNode;
  title: string;
  sub?: string;
  children: ReactNode;
}

export function PageShell({ crumbs, actions, title, sub, children }: PageShellProps) {
  const here = crumbs?.[crumbs.length - 1];
  const trail = crumbs?.slice(0, -1) ?? [];
  return (
    <>
      {crumbs && crumbs.length > 0 && (
        <header className="topbar">
          <div className="topbar__crumbs">
            {trail.map((c, i) => (
              <span key={i} className="crumb-link">{c}</span>
            ))}
            {trail.length > 0 && (
              <svg className="chev" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
            )}
            <span className="topbar__here">{here}</span>
          </div>
          {actions && <div className="topbar__actions">{actions}</div>}
        </header>
      )}
      <div className="page-head">
        <div>
          <h1 className="page-title">{title}</h1>
          {sub && <p className="page-sub">{sub}</p>}
        </div>
        {!crumbs && actions && <div className="topbar__actions">{actions}</div>}
      </div>
      {children}
    </>
  );
}
