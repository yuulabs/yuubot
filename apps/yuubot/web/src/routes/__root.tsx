// __root.tsx — app shell root route (ISSUE-0007 S2).
//
// Renders the demo-aligned app shell: sidebar (brand mark + two nav groups
// 运行时 / 系统 + runner footer with daemon address) and a topbar (breadcrumb
// group › here + Refresh + child-route-injected actions). The layout chrome
// + actions context + shell CSS live in components/baseline/AppShell.tsx;
// this route authors the nav/runner/topbar CONTENT (DVR for the source-marker
// test) and consumes useHealth for the runner footer.
import { createRootRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  Activity,
  ArrowRightToLine,
  Box,
  LayoutGrid,
  Plug,
  RefreshCw,
  Server,
  SlidersHorizontal,
} from "lucide-react";
import { AppShell, useAppShellActions } from "@/components/baseline";
import { useHealth } from "@/hooks/use-resources";

// ---------------------------------------------------------------------------
// Nav model — aligned to demo-playground sidebar (运行时 / 系统).
// ---------------------------------------------------------------------------

type NavGroupLabel = "运行时" | "系统";
type NavItem = {
  to: string;
  label: string;
  icon: typeof Box;
  group: NavGroupLabel;
};

const NAV_ITEMS: readonly NavItem[] = [
  // 运行时 — demo's runtime group (Integrations added: existing route, kept).
  { to: "/actors", label: "Actors", icon: Box, group: "运行时" },
  { to: "/routes", label: "Ingress", icon: ArrowRightToLine, group: "运行时" },
  { to: "/capability-sets", label: "Capability Sets", icon: LayoutGrid, group: "运行时" },
  { to: "/providers", label: "Providers", icon: Server, group: "运行时" },
  { to: "/integrations", label: "Integrations", icon: Plug, group: "运行时" },
  // 系统 — demo's system group.
  { to: "/monitor", label: "Traces", icon: Activity, group: "系统" },
  { to: "/settings", label: "Settings", icon: SlidersHorizontal, group: "系统" },
];

function resolveCrumbs(pathname: string): { group: string; here: string } {
  let best: { group: string; here: string } | null = null;
  let bestLen = -1;
  for (const item of NAV_ITEMS) {
    if (pathname === item.to || pathname.startsWith(item.to + "/")) {
      if (item.to.length > bestLen) {
        best = { group: item.group, here: item.label };
        bestLen = item.to.length;
      }
    }
  }
  return best ?? { group: "系统", here: "—" };
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function Brand() {
  return (
    <div className="brand">
      <div className="brand__mark">y</div>
      <div className="brand__name">
        <span className="brand__title">yuubot</span>
        <span className="brand__sub">control plane</span>
      </div>
    </div>
  );
}

// Explicit nav links — authored as literal `<Link to="/…">` JSX so the S2
// source-marker test can assert each of the seven demo routes is wired.
function RuntimeNavLinks() {
  return (
    <>
      <Link to="/actors" className="nav__item" activeProps={{ className: "is-active" }}>
        <Box size={16} className="nav__icon" />
        <span>Actors</span>
      </Link>
      <Link to="/routes" className="nav__item" activeProps={{ className: "is-active" }}>
        <ArrowRightToLine size={16} className="nav__icon" />
        <span>Ingress</span>
      </Link>
      <Link to="/capability-sets" className="nav__item" activeProps={{ className: "is-active" }}>
        <LayoutGrid size={16} className="nav__icon" />
        <span>Capability Sets</span>
      </Link>
      <Link to="/providers" className="nav__item" activeProps={{ className: "is-active" }}>
        <Server size={16} className="nav__icon" />
        <span>Providers</span>
      </Link>
      <Link to="/integrations" className="nav__item" activeProps={{ className: "is-active" }}>
        <Plug size={16} className="nav__icon" />
        <span>Integrations</span>
      </Link>
    </>
  );
}

function SystemNavLinks() {
  return (
    <>
      <Link to="/monitor" className="nav__item" activeProps={{ className: "is-active" }}>
        <Activity size={16} className="nav__icon" />
        <span>Traces</span>
      </Link>
      <Link to="/settings" className="nav__item" activeProps={{ className: "is-active" }}>
        <SlidersHorizontal size={16} className="nav__icon" />
        <span>Settings</span>
      </Link>
    </>
  );
}

function NavGroupExplicit({ label, children }: { label: NavGroupLabel; children: ReactNode }) {
  return (
    <div className="nav__group">
      <span className="nav__group-label">{label}</span>
      {children}
    </div>
  );
}

function Runner({ daemon, status }: { daemon?: string; status?: string }) {
  const ok = !!status && /ok|healthy|up/i.test(status);
  return (
    <div className="sidebar__footer">
      <div className="runner">
        <div
          className="runner__dot"
          style={ok ? undefined : { background: "var(--text-3)" }}
        />
        <div className="runner__text">
          <span>daemon</span>
          <small>{daemon ?? "—"}</small>
        </div>
      </div>
    </div>
  );
}

function Sidebar({ daemon, status }: { daemon?: string; status?: string }) {
  return (
    <aside className="sidebar">
      <Brand />
      <nav className="nav">
        <NavGroupExplicit label="运行时">
          <RuntimeNavLinks />
        </NavGroupExplicit>
        <NavGroupExplicit label="系统">
          <SystemNavLinks />
        </NavGroupExplicit>
      </nav>
      <Runner daemon={daemon} status={status} />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Topbar — breadcrumb (group › here) + Refresh + injected actions.
// ---------------------------------------------------------------------------

function Topbar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const queryClient = useQueryClient();
  const { actions } = useAppShellActions();
  const { group, here } = resolveCrumbs(pathname);
  return (
    <header className="topbar">
      <div className="topbar__crumbs">
        <span>{group}</span>
        <svg className="chev" viewBox="0 0 24 24">
          <path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth={1.6} />
        </svg>
        <span className="topbar__here">{here}</span>
      </div>
      <div className="topbar__actions">
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => queryClient.invalidateQueries()}
          aria-label="刷新"
        >
          <RefreshCw size={15} />
          <span>刷新</span>
        </button>
        {actions}
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Root route
// ---------------------------------------------------------------------------

export const Route = createRootRoute({ component: RootLayout });

function RootLayout() {
  const { data: health } = useHealth();
  return (
    <AppShell
      sidebar={<Sidebar daemon={health?.daemon} status={health?.status} />}
      topbar={<Topbar />}
    >
      <Outlet />
    </AppShell>
  );
}
