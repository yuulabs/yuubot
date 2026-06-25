import { useState } from "react";
import { createRootRoute, Link, Outlet } from "@tanstack/react-router";
import {
  Bot,
  CircleDot,
  Layers,
  LayoutDashboard,
  MessageSquare,
  PanelLeft,
  PanelLeftClose,
  Plug,
  Route as RouteIcon,
  Settings,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

const navGroups = [
  {
    label: "Overview",
    items: [
      { to: "/", icon: LayoutDashboard, label: "Dashboard" },
      { to: "/admin/conversations", icon: MessageSquare, label: "Admin Conversation" },
    ],
  },
  {
    label: "Resources",
    items: [
      { to: "/actors", icon: Bot, label: "Actors" },
      { to: "/capability-sets", icon: Layers, label: "Capability Sets" },
      { to: "/routes", icon: RouteIcon, label: "Ingress Rules" },
    ],
  },
  {
    label: "Providers",
    items: [
      { to: "/providers", icon: Zap, label: "LLM Backends" },
      { to: "/integrations", icon: Plug, label: "Integrations" },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/monitor", icon: CircleDot, label: "Monitor" },
      { to: "/settings", icon: Settings, label: "Settings" },
    ],
  },
];

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  return (
    <div className="flex h-screen bg-background">
      {!sidebarCollapsed && <Sidebar />}
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
        />
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r bg-card">
      <div className="flex h-14 items-center gap-2 px-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
          Y
        </div>
        <span className="font-semibold">yuubot</span>
        <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          admin
        </span>
      </div>
      <Separator />
      <nav className="flex-1 space-y-4 overflow-auto p-3">
        {navGroups.map((group) => (
          <div key={group.label} className="space-y-1">
            <div className="px-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {group.label}
            </div>
            {group.items.map((item) => {
              const NavIcon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground [&.active]:bg-accent [&.active]:text-foreground"
                  activeProps={{ className: "bg-accent text-foreground" }}
                >
                  <NavIcon size={17} />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <Separator />
      <div className="flex items-center gap-2 px-4 py-3">
        <div className="flex size-7 items-center justify-center rounded-full bg-muted text-xs font-medium">
          A
        </div>
        <span className="text-sm font-medium">admin</span>
      </div>
    </aside>
  );
}

function Topbar({
  sidebarCollapsed,
  onToggleSidebar,
}: {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}) {
  const ToggleIcon = sidebarCollapsed ? PanelLeft : PanelLeftClose;
  return (
    <header className="flex h-11 shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleSidebar}
          aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
        >
          <ToggleIcon className="size-4" />
        </Button>
        <span className="text-sm font-medium">Dashboard</span>
        <span className="text-xs text-muted-foreground">Overview</span>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="xs">
          Refresh
        </Button>
      </div>
    </header>
  );
}
