// capability-sets.$id.edit.tsx — `/capability-sets/$id/edit` editor (ISSUE-0007 S5).
//
// Edit-state Capability Set editor: same demo-aligned body as the new editor
// (editor__hero + editor__cols with 基本 + 能力 CapTree + rail) but prefilled
// from the existing Capability Set, calling `useUpdateResource` on save and
// exposing the danger zone (delete) in the rail.
//
// CapTree is provided by S1 (`components/baseline/CapTree`); this route only
// assembles `groups` from `useLiveCapabilities()` and feeds them in.
import { useEffect, useMemo, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  useDeleteResource,
  useLiveCapabilities,
  useResourceList,
  useUpdateResource,
} from "@/hooks/use-resources";
import type { CapabilitySetResource, LiveCapability } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  CapTree,
  Field,
  LegendCard,
  PageShell,
  RailCard,
  useAppShellActions,
} from "@/components/baseline";

export const Route = createFileRoute("/capability-sets/$id/edit")({
  component: CapabilitySetEditPage,
});

interface CapGroup {
  sourceId: string;
  sourceName: string;
  capabilities: {
    capabilityId: string;
    name: string;
    description: string;
  }[];
}

/** Group live capabilities by integration source (CapTree groups contract). */
function groupByIntegration(caps: LiveCapability[]): CapGroup[] {
  const bySource = new Map<string, LiveCapability[]>();
  for (const c of caps) {
    const bucket = bySource.get(c.integration_id) ?? [];
    bucket.push(c);
    bySource.set(c.integration_id, bucket);
  }
  return Array.from(bySource.entries()).map(([sourceId, list]) => ({
    sourceId,
    sourceName: list[0]?.integration_name ?? sourceId,
    capabilities: list.map((c) => ({
      capabilityId: c.capability_id,
      name: c.capability_name || c.capability_id,
      description: c.description,
    })),
  }));
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  return parts.map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

function CapabilitySetEditPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { setActions } = useAppShellActions();
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const { data: liveCapabilities = [] } = useLiveCapabilities();
  const updateMutation = useUpdateResource<CapabilitySetResource>("capability-sets");
  const deleteMutation = useDeleteResource("capability-sets");

  const cs = capabilitySets.find((item) => item.id === id);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [rolloverEnabled, setRolloverEnabled] = useState(false);
  const [idleTimeout, setIdleTimeout] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);

  // Prefill once the resource lands.
  useEffect(() => {
    if (cs && !hydrated) {
      setName(cs.name);
      setDescription(cs.description);
      setWorkspacePath(cs.workspace_path ?? "");
      setRolloverEnabled(!!cs.loop_policy?.rollover_enabled);
      setIdleTimeout(
        cs.loop_policy?.idle_timeout_s
          ? String(cs.loop_policy.idle_timeout_s)
          : "",
      );
      setSelectedIds(cs.integration_ids ?? []);
      setHydrated(true);
    }
  }, [cs, hydrated]);

  const capGroups = useMemo(
    () => groupByIntegration(liveCapabilities),
    [liveCapabilities],
  );

  const cancel = () => navigate({ to: "/capability-sets" });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await updateMutation.mutateAsync({
      id,
      data: {
        name,
        description,
        integration_ids: selectedIds,
        workspace_path: workspacePath,
        loop_policy: {
          rollover_enabled: rolloverEnabled,
          idle_timeout_s: Number(idleTimeout) || 0,
        },
      },
    });
    navigate({ to: "/capability-sets" });
  };

  const handleDelete = () => {
    if (!cs) return;
    if (!confirm(`删除「${cs.name}」？`)) return;
    deleteMutation.mutate(id, {
      onSuccess: () => navigate({ to: "/capability-sets" }),
    });
  };

  // Cancel / Save pushed into the shell topbar.
  useEffect(() => {
    setActions(
      <>
        <Button onClick={cancel} className="btn btn--ghost">取消</Button>
        <Button
          type="submit"
          form="capset-editor-form"
          disabled={updateMutation.isPending}
          className="btn btn--primary"
        >
          {updateMutation.isPending ? "保存中…" : "保存"}
        </Button>
      </>,
    );
    return () => setActions(null);
  }, [setActions, updateMutation.isPending]);

  if (!cs) {
    return <PageShell title="编辑 Capability Set">未找到 Capability Set「{id}」。</PageShell>;
  }

  return (
    <PageShell
      title={`编辑 · ${cs.name || cs.id}`}
      sub="调整集合能力、工作区与策略；保存即生效，引用此集合的 Actor 将获得最新声明的能力。"
    >
      <form id="capset-editor-form" className="editor" onSubmit={handleSubmit} autoComplete="off">
        <div className="editor__hero">
          <div className="hero__avatar hero__avatar--icon">
            {initials(name) || "CS"}
          </div>
          <div className="hero__fields">
            <Field label="名称" inline>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：默认工具集"
                required
              />
            </Field>
            <Field label="一句话描述" inline>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="这个集合提供哪些能力？"
              />
            </Field>
            <div className="hero__meta">
              <span className="kv"><b>已选</b> <code>{selectedIds.length}</code> 项能力</span>
              <span className="kv"><b>ID</b> <code>{cs.id}</code></span>
            </div>
          </div>
        </div>

        <div className="editor__cols">
          <div className="editor__main">
            <LegendCard dotColor="indigo" legend="基本">
              <div className="grid-2">
                <Field label="工作区路径">
                  <Input
                    value={workspacePath}
                    onChange={(e) => setWorkspacePath(e.target.value)}
                    placeholder="例如：default"
                    className="font-mono"
                  />
                </Field>
                <Field label="空闲超时 (秒)">
                  <Input
                    type="number"
                    min="0"
                    step="1"
                    value={idleTimeout}
                    onChange={(e) => setIdleTimeout(e.target.value)}
                    placeholder="0 = 不超时"
                  />
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm" style={{ marginTop: "var(--sp-4)" }}>
                <input
                  type="checkbox"
                  checked={rolloverEnabled}
                  onChange={(e) => setRolloverEnabled(e.target.checked)}
                  className="size-4 rounded border-input"
                />
                启用历史压缩 (rollover)
              </label>
            </LegendCard>

            <LegendCard dotColor="green" legend="能力">
              <p className="card__lead">按来源分组，可整组勾选。展开查看具体能力。</p>
              <CapTree
                groups={capGroups}
                selectedIds={selectedIds}
                onChange={setSelectedIds}
              />
            </LegendCard>
          </div>

          <aside className="editor__rail">
            <RailCard
              title="提示"
              lead="保存后，在 Actor 编辑器的「Capability Set」下拉里即可选择此集合，绑定后获得全部声明的能力。"
            >
              {null}
            </RailCard>
            <RailCard title="危险操作" danger>
              <Button
                type="button"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className="btn btn--ghost"
                style={{ color: "var(--rose)", borderColor: "var(--rose)" }}
              >
                删除此集合
              </Button>
            </RailCard>
          </aside>
        </div>

        {updateMutation.error && (
          <p className="text-xs text-destructive">{updateMutation.error.message}</p>
        )}
      </form>
    </PageShell>
  );
}
