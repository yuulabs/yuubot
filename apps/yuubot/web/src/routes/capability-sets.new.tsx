// capability-sets.new.tsx — `/capability-sets/new` editor (ISSUE-0007 S5).
//
// Demo-aligned Capability Set editor (新建态): editor__hero (CS avatar mark +
// name/description inline fields + selected-cap-count + ID) + editor__cols
// (main: 基本 LegendCard with workspace path / daily budget / memory toggle;
// 能力 LegendCard with lead + CapTree grouped by integration source) + rail
// (提示 card only — danger zone is edit-only).
//
// CapTree is provided by S1 (`components/baseline/CapTree`); this route only
// assembles `groups` from `useLiveCapabilities()` and feeds them in. The Save
// action is pushed into the shell topbar (the shell owns the topbar, per the
// S2 app-shell reconciliation note).
import { useEffect, useMemo, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  useCreateResource,
  useLiveCapabilities,
} from "@/hooks/use-resources";
import type { LiveCapability } from "@/types/api";
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

export const Route = createFileRoute("/capability-sets/new")({
  component: CapabilitySetNewPage,
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
  const head = parts.map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  return head;
}

function CapabilitySetNewPage() {
  const navigate = useNavigate();
  const { setActions } = useAppShellActions();
  const { data: liveCapabilities = [] } = useLiveCapabilities();
  const createMutation = useCreateResource("capability-sets");

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [budget, setBudget] = useState("");
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const capGroups = useMemo(
    () => groupByIntegration(liveCapabilities),
    [liveCapabilities],
  );

  const cancel = () => navigate({ to: "/capability-sets" });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({
      name,
      description,
      integration_capability_ids: selectedIds,
      workspace_path: workspacePath,
      runtime_policy: { memory_enabled: memoryEnabled },
      resource_policy: { budget_usd_daily: Number(budget) || null },
    });
    navigate({ to: "/capability-sets" });
  };

  // Cancel / Save pushed into the shell topbar.
  useEffect(() => {
    setActions(
      <>
        <Button onClick={cancel} className="btn btn--ghost">取消</Button>
        <Button
          type="submit"
          form="capset-editor-form"
          disabled={createMutation.isPending}
          className="btn btn--primary"
        >
          {createMutation.isPending ? "保存中…" : "保存"}
        </Button>
      </>,
    );
    return () => setActions(null);
  }, [setActions, createMutation.isPending]);

  return (
    <PageShell title="新建 Capability Set" sub="声明一组能力，Actor 引用此集合即获得全部声明的能力。">
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
              <span className="kv"><b>ID</b> <code>—</code></span>
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
                <Field label="日预算 (USD)">
                  <Input
                    type="number"
                    min="0"
                    step="0.01"
                    value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                    placeholder="0 = 不限"
                  />
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm" style={{ marginTop: "var(--sp-4)" }}>
                <input
                  type="checkbox"
                  checked={memoryEnabled}
                  onChange={(e) => setMemoryEnabled(e.target.checked)}
                  className="size-4 rounded border-input"
                />
                启用内存
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
          </aside>
        </div>

        {createMutation.error && (
          <p className="text-xs text-destructive">{createMutation.error.message}</p>
        )}
      </form>
    </PageShell>
  );
}
