// actors.tsx — /actors browse view (ISSUE-0007 S3).
//
// Mirrors demo `view--browse`: page-head (title + sub) + toolbar (SearchBox +
// SegFilter + LayoutToggle) + grid/list render + Empty. Creation lives in
// /actors/new; the row/edit actions route there. The shell-level topbar
// (crumbs + Refresh) is rendered by __root; this route only injects the
// "新建 Actor" action via useAppShellActions.
//
// Schema deviation D-extra: ActorResource carries only `enabled: boolean`, so
// the demo's draft/paused/running tri-state collapses to running(enabled) /
// 已停止(disabled) + 全部. The segment labels keep the demo three-segment shape
// (全部/运行中/已停止) but draft folds into 已停止.
import { useEffect, useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { MessageSquare, Pencil } from "lucide-react";
import { useResourceList } from "@/hooks/use-resources";
import type {
  ActorResource,
  CapabilitySetResource,
  LLMBackendResource,
} from "@/types/api";
import {
  Empty,
  LayoutToggle,
  SearchBox,
  SegFilter,
  StatusPill,
  useAppShellActions,
} from "@/components/baseline";

export const Route = createFileRoute("/actors")({
  component: ActorsBrowsePage,
});

type StatusFilter = "all" | "running" | "stopped";
type Layout = "grid" | "list";

function ActorsBrowsePage() {
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");

  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [layout, setLayout] = useState<Layout>("grid");

  // Push the "新建 Actor" primary action into the shell topbar.
  const { setActions } = useAppShellActions();
  useEffect(() => {
    setActions(
      <Link to="/actors/new">
        <button type="button" className="btn btn--primary">
          <span>新建 Actor</span>
        </button>
      </Link>,
    );
    return () => setActions(null);
  }, [setActions]);

  const backendName = (id?: string) =>
    backends.find((b) => b.id === id)?.name ?? "—";
  const capsetName = (id?: string) =>
    capabilitySets.find((c) => c.id === id)?.name ?? "—";

  const counts = useMemo(
    () => ({
      all: actors.length,
      running: actors.filter((a) => a.enabled).length,
      stopped: actors.filter((a) => !a.enabled).length,
    }),
    [actors],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return actors
      .filter((a) => {
        if (status === "running") return a.enabled;
        if (status === "stopped") return !a.enabled;
        return true;
      })
      .filter((a) => {
        if (!q) return true;
        const hay = [
          a.name,
          a.default_model,
          backendName(a.default_llm_backend?.id),
          capsetName(a.capability_set?.id),
        ]
          .join(" ")
          .toLowerCase();
        return hay.includes(q);
      });
  }, [actors, query, status, backends, capabilitySets]);

  return (
    <div className="view">
      <div className="page-head">
        <div>
          <h1 className="page-title">Actors</h1>
          <p className="page-sub">
            运行中的 Agent 实例。每个 Actor 绑定一个 Agent 规格与一个 Capability Set，通过 Ingress 规则接收事件并产出回合。点开 Actor 即可发起对话。
          </p>
        </div>
      </div>

      {/* toolbar: search + status seg + layout toggle */}
      <div className="toolbar">
        <SearchBox value={query} onChange={setQuery} placeholder="按名称、Agent、模型搜索…" />
        <SegFilter<StatusFilter>
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "全部", count: counts.all },
            { value: "running", label: "运行中", count: counts.running },
            { value: "stopped", label: "已停止", count: counts.stopped },
          ]}
        />
        <LayoutToggle value={layout} onChange={setLayout} />
      </div>

      {filtered.length === 0 ? (
        <Empty
          illustration="No match"
          title="没有匹配的 Actor"
          description="试试调整筛选条件，或新建一个 Actor。"
          action={
            <Link to="/actors/new">
              <button type="button" className="btn btn--primary">新建 Actor</button>
            </Link>
          }
        />
      ) : (
        <div className="actors" data-layout={layout}>
          {filtered.map((actor) => (
            <ActorCard
              key={actor.id}
              actor={actor}
              backendName={backendName(actor.default_llm_backend?.id)}
              capsetName={capsetName(actor.capability_set?.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ActorCard({
  actor,
  backendName,
  capsetName,
}: {
  actor: ActorResource;
  backendName: string;
  capsetName: string;
}) {
  const avatar = (actor.name.trim()[0] ?? "A").toUpperCase();
  // ISSUE-0010: actor-bound draft route is the sole conversation entry point.
  return (
    <article className="actor-card">
      <header className="ac__top">
        <div className="ac__avatar">{avatar}</div>
        <div className="ac__head">
          <Link to="/actors/$id" params={{ id: actor.id }} className="ac__name">
            {actor.name}
          </Link>
          <span className="ac__id"><code>{actor.id}</code></span>
        </div>
        <StatusPill variant={actor.enabled ? "running" : "paused"}>
          {actor.enabled ? "运行中" : "已停止"}
        </StatusPill>
      </header>
      <div className="ac__body">
        <div className="ac__row"><span>模型</span><code>{actor.default_model}</code></div>
        <div className="ac__row"><span>Backend</span>{backendName}</div>
        <div className="ac__row">
          <span>Capability Set</span>
          <Link
            to="/capability-sets"
            className="inline-link"
          >
            {capsetName}
          </Link>
        </div>
        {/* workspace column regression anchor (conversation-entry-via-actor test). */}
        <div className="ac__row">
          <span>Workspace</span>
          {actor.capability_set?.workspace_path ? (
            <a
              href={`/workspace/${actor.capability_set.workspace_path}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-link"
            >
              {actor.capability_set.workspace_path}
            </a>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </div>
        <div className="ac__row"><span>步数上限</span>{actor.default_budget?.max_steps ?? "—"}</div>
      </div>
      <footer className="ac__quick">
        <Link
          to="/admin/conversations/$conversationId"
          params={{ conversationId: `actor-${actor.id}` }}
        >
          <button type="button" className="btn btn--ghost">
            <MessageSquare size={14} />
            <span>发起对话</span>
          </button>
        </Link>
        <Link to="/actors/$id" params={{ id: actor.id }}>
          <button type="button" className="btn btn--ghost">详情</button>
        </Link>
        <Link to="/actors/$id/edit" params={{ id: actor.id }}>
          <button type="button" className="btn btn--ghost">
            <Pencil size={14} />
            <span>编辑</span>
          </button>
        </Link>
      </footer>
    </article>
  );
}
