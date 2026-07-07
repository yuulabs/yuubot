import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Toaster } from "sonner";

import {
  Activity,
  ArrowRightToLine,
  Bot,
  BookOpen,
  Clock,
  DatabaseZap,
  FolderOpen,
  Menu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  RefreshCw,
  Server,
  Settings,
  Share2,
  SquareTerminal,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useBootstrap, useNotificationListener, useRefreshBootstrap, useSidebar } from "@/shared/hooks";

interface TopbarActionsContextValue {
  setActions: (node: ReactNode | null) => void;
}

const TopbarActionsContext = createContext<TopbarActionsContextValue | null>(null);

export function useTopbarActions(): TopbarActionsContextValue {
  const context = useContext(TopbarActionsContext);
  if (!context) {
    throw new Error("useTopbarActions must be used within AppLayout");
  }
  return context;
}

export const navItems = [
  { to: "/actors", label: "Actors", icon: Bot },
  { to: "/workspace", label: "Workspace", icon: FolderOpen },
  { to: "/providers", label: "Providers", icon: Server },
  { to: "/integrations", label: "Integrations", icon: Plug },
  { to: "/mcp-servers", label: "MCP Servers", icon: DatabaseZap },
  { to: "/skills", label: "Skills", icon: BookOpen },
  { to: "/routes", label: "Routes", icon: ArrowRightToLine },
  { to: "/admin/conversations", label: "Conversations", icon: MessageSquare },
  { to: "/monitor", label: "Runtime", icon: Activity },
  { to: "/cron", label: "Cron Jobs", icon: Clock },
  { to: "/shares", label: "Shares", icon: Share2 },
  { to: "/terminal", label: "Terminal", icon: SquareTerminal },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export function AppLayout() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { data } = useBootstrap();
  const refresh = useRefreshBootstrap();
  useNotificationListener();
  const { collapsed, mobileOpen, isMobile, toggleDesktop, toggleMobile, closeMobile } = useSidebar();
  const [topbarActions, setTopbarActions] = useState<ReactNode | null>(null);
  const topbarActionsValue = useMemo(() => ({ setActions: setTopbarActions }), []);
  const current = navItems.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`));
  const sidebarCollapsed = collapsed && !isMobile;
  const conversationTitle = getConversationTitle(pathname, data);

  useEffect(() => {
    if (isMobile) {
      closeMobile();
    }
  }, [pathname, isMobile, closeMobile]);

  return (
    <TopbarActionsContext.Provider value={topbarActionsValue}>
    <div
      className={[
        "app",
        sidebarCollapsed ? "app--sidebar-collapsed" : "",
        mobileOpen ? "app--sidebar-open" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <button
        type="button"
        className="sidebar-backdrop"
        aria-label="Close navigation"
        onClick={closeMobile}
      />
      <aside className={["sidebar", sidebarCollapsed ? "sidebar--collapsed" : ""].filter(Boolean).join(" ")}>
        <div className="brand">
          <div className="brand__mark">y</div>
          <div className="brand__name">
            <span className="brand__title">yuubot</span>
            <span className="brand__sub">control plane</span>
          </div>
          <button
            type="button"
            className="sidebar__toggle"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!sidebarCollapsed}
            onClick={toggleDesktop}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={16} />}
          </button>
        </div>
        <nav className="nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                className="nav__item"
                activeProps={{ className: "is-active" }}
                title={sidebarCollapsed ? item.label : undefined}
              >
                <Icon size={sidebarCollapsed ? 20 : 16} className="nav__icon" />
                <span className="nav__label">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar__footer">
          <div className="runner" title={sidebarCollapsed && data ? `schema ${data.schema_version}` : undefined}>
            <div className="runner__dot" />
            <div className="runner__text">
              <span>server</span>
              <small>{data ? `schema ${data.schema_version}` : "loading"}</small>
            </div>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar__crumbs">
            <button
              type="button"
              className="topbar__menu"
              aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
              aria-expanded={mobileOpen}
              onClick={toggleMobile}
            >
              <Menu size={18} />
            </button>
            <span className="crumb-link">yuubot</span>
            <svg className="chev" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
            <span className="topbar__here">{current?.label ?? "Dashboard"}</span>
            {conversationTitle && (
              <>
                <svg className="chev" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
                <span className="topbar__detail" title={conversationTitle}>{conversationTitle}</span>
              </>
            )}
          </div>
          <div className="topbar__actions">
            {topbarActions}
            <Button variant="outline" size="sm" onClick={refresh}>
              <RefreshCw size={14} />
              <span>Refresh</span>
            </Button>
          </div>
        </header>
        <Outlet />
      </main>
      <Toaster richColors closeButton position="bottom-right" />
    </div>
    </TopbarActionsContext.Provider>
  );
}

function getConversationTitle(
  pathname: string,
  data: ReturnType<typeof useBootstrap>["data"],
): string {
  const match = pathname.match(/^\/admin\/conversations\/([^/]+)$/);
  if (!match) return "";
  const conversationId = decodeURIComponent(match[1] ?? "");
  if (!conversationId) return "";
  if (conversationId === "new") {
    const actorId = new URLSearchParams(window.location.search).get("actor") ?? "";
    const actor = data?.actors.find((item) => item.id === actorId);
    const actorName = actor?.name || actorId || "Conversation";
    return `${actorName} / New conversation`;
  }
  const summary = data?.conversations.find((item) => item.id === conversationId);
  if (summary?.title) return summary.title;
  const actorId = summary?.actor_id ?? "";
  const actor = data?.actors.find((item) => item.id === actorId);
  const actorName = actor?.name || actorId || "Conversation";
  return `${actorName} / ${conversationId}`;
}
