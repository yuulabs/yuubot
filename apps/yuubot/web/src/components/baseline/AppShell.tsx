// AppShell — the demo-aligned app shell skeleton.
//
// Owns the `.app` (sidebar + main) grid, the shell-CSS carrier for the demo
// Pearl/cyan/yellow design (lifted verbatim from ../../../../demo-playground/
// styles.css, token-resolved via index.css :root), and the actions-injection
// context that lets child routes push right-side buttons into the topbar.
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
// Shell CSS — demo app-shell + nav + runner + topbar + btn, verbatim.
// ---------------------------------------------------------------------------

const SHELL_CSS = `
/* ---------- App shell ---------- */
.app {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100dvh;
  background: var(--page-bg);
  background-color: var(--bg);
}

/* ---------- Sidebar ---------- */
.sidebar {
  background: linear-gradient(180deg, #ffffff 0%, var(--bg-2) 100%);
  border-right: 1px solid var(--border-hi);
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-6);
  position: sticky;
  top: 0;
  height: 100vh;
}
.brand { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2); }
.brand__mark {
  width: 32px; height: 32px;
  display: grid; place-items: center;
  font-family: var(--ff-display);
  font-weight: 700; font-size: 16px;
  color: var(--yellow);
  background: var(--navy);
  border-radius: var(--r-md);
  box-shadow: inset 0 0 0 1.5px var(--cyan);
}
.brand__name { display: flex; flex-direction: column; line-height: 1.15; }
.brand__title { font-family: var(--ff-display); font-weight: 700; color: var(--ink); }
.brand__sub { font-size: 11px; color: var(--cyan-deep); font-weight: 600; }

.nav { display: flex; flex-direction: column; gap: var(--sp-6); flex: 1; }
.nav__group { display: flex; flex-direction: column; gap: 1px; }
.nav__group-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--cyan-deep); padding: 0 var(--sp-3); margin-bottom: var(--sp-1);
  font-weight: 700;
}
.nav__item {
  display: flex; align-items: center; gap: var(--sp-3);
  padding: 7px var(--sp-3);
  border-radius: var(--r-md);
  color: var(--text-2);
  text-decoration: none;
  font-weight: 500;
  border: 1px solid transparent;
  transition: background .16s, color .16s, border-color .16s, box-shadow .16s;
}
.nav__icon { width: 16px; height: 16px; }
.nav__item:hover { background: #fff; border-color: var(--border-hi); color: var(--cyan-deep); }
.nav__item.is-active {
  background: var(--navy);
  color: #fff;
  border-color: transparent;
  box-shadow: inset 3px 0 0 var(--yellow), var(--shadow-hi);
}
.nav__item.is-active .nav__icon { stroke: var(--yellow); }

.sidebar__footer { display: flex; flex-direction: column; gap: var(--sp-3); }
.runner {
  display: flex; align-items: center; gap: var(--sp-3);
  padding: 7px var(--sp-3); border-radius: var(--r-md);
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
}
.runner__dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green);
  flex-shrink: 0;
}
.runner__text { display: flex; flex-direction: column; line-height: 1.15; }
.runner__text span { font-weight: 500; font-size: 12px; }
.runner__text small { color: var(--cyan-deep); font-size: 11px; }

/* ---------- Main + view ---------- */
.main { min-width: 0; min-height: 100dvh; position: relative; }
.view { padding: var(--sp-6) var(--sp-8) var(--sp-10); max-width: 1280px; }

/* ---------- Topbar (shell-level: crumbs + actions) ---------- */
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--sp-3) var(--sp-8);
  position: sticky; top: 0; z-index: 20;
  background: rgba(251, 253, 255, 0.94);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(14px);
}
.topbar__crumbs { display: flex; align-items: center; gap: var(--sp-2); color: var(--text-3); font-weight: 500; }
.chev { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; }
.topbar__here { color: var(--text); font-weight: 700; }
.topbar__actions { display: flex; gap: var(--sp-2); }

/* ---------- Buttons ---------- */
.btn {
  display: inline-flex; align-items: center; gap: var(--sp-2);
  padding: 7px 13px; border-radius: var(--r-md);
  font-weight: 500; font-size: 13px; cursor: pointer;
  border: 1px solid transparent;
  transition: background .16s, border-color .16s, box-shadow .16s, opacity .16s, color .16s;
  white-space: nowrap;
  font-family: inherit;
}
.btn:active { opacity: .85; transform: translateY(1px); }
.btn svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.6; }
.btn--primary {
  background: linear-gradient(180deg, #f9d566 0%, var(--yellow) 100%);
  color: #5a4408;
  border-color: var(--yellow-deep);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), var(--shadow);
  font-weight: 700;
}
.btn--primary:hover { background: linear-gradient(180deg, #fbdd80 0%, #f7cf52 100%); box-shadow: inset 0 1px 0 rgba(255,255,255,.6), var(--shadow-hi); }
.btn--ghost { background: var(--surface); color: var(--text); border-color: var(--border-hi); }
.btn--ghost:hover { border-color: var(--cyan-deep); color: var(--cyan-deep); background: var(--surface-2); }
`;

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
      <style>{SHELL_CSS}</style>
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
