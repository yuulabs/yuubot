// AppShell — the demo-aligned app shell skeleton.
//
// Owns the `.app` (sidebar + main) grid and the actions-injection context
// that lets child routes push right-side buttons into the topbar. The demo
// shell chrome (sidebar / brand / nav / runner / topbar / btn) is styled by
// the comprehensive structural port in styles/baseline.css, which is the
// single source of truth for all demo structural CSS (the inline <style>
// carrier previously held here was consolidated into baseline.css during
// the ISSUE-0007 CSS-gap direct fix).
//
// The sidebar/topbar CONTENT (brand, nav links, runner footer, crumbs) is
// authored by the route consuming AppShell (apps/yuubot/web/src/routes/
// __root.tsx) and passed in as slots — that keeps the nav contract (demo
// groups 运行时/系统 + the seven links + the daemon footer) visible in the
// route source where issue S2's source-marker test asserts it.
import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------------------
// Actions injection — child routes push right-side actions into the topbar.
// ---------------------------------------------------------------------------

interface AppShellActionsContextValue {
  /** Current actions (read by the topbar). */
  actions: ReactNode | null;
  /** Push (or clear with null) the right-side actions. */
  setActions: (node: ReactNode | null) => void;
}

export const AppShellActionsContext =
  createContext<AppShellActionsContextValue | null>(null);

/**
 * Read (for the topbar) or push (for child routes) topbar actions. Typical
 * child-route use:
 *
 *   const { setActions } = useAppShellActions();
 *   useEffect(() => {
 *     setActions(<Button onClick={onCreate}>新建 Actor</Button>);
 *     return () => setActions(null);
 *   }, [setActions]);
 */
export function useAppShellActions(): AppShellActionsContextValue {
  const ctx = useContext(AppShellActionsContext);
  if (!ctx) {
    throw new Error("useAppShellActions must be used within <AppShell>");
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export interface AppShellProps {
  sidebar: ReactNode;
  topbar: ReactNode;
  children?: ReactNode;
}

export function AppShell({ sidebar, topbar, children }: AppShellProps) {
  const [actions, setActions] = useState<ReactNode | null>(null);
  const actionsValue = useMemo<AppShellActionsContextValue>(
    () => ({ actions, setActions }),
    [actions],
  );

  return (
    <AppShellActionsContext.Provider value={actionsValue}>
      <div className="app">
        {sidebar}
        <main className="main">
          {topbar}
          {children}
        </main>
      </div>
    </AppShellActionsContext.Provider>
  );
}
