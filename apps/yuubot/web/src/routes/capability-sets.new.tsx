// capability-sets.new.tsx — `/capability-sets/new` editor (ISSUE-0007 S5).
//
// Demo-aligned Capability Set editor (新建态): editor__hero (CS avatar mark +
// name/description inline fields + selected integration count + ID) + editor__cols
// (main: 基本 LegendCard with workspace path / loop policy; 能力 LegendCard
// with flat integration-instance selection) + rail.
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

const ALL_INTEGRATIONS_SENTINEL = "*";

interface CapGroup {
  sourceId: string;
  sourceName: string;
  capabilities: {
    capabilityId: string;
    name: string;
    description: string;
  }[];
}

/** Group live capabilities by integration instance (selection value = id). */
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
  const [rolloverEnabled, setRolloverEnabled] = useState(false);
  const [idleTimeout, setIdleTimeout] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [allIntegrations, setAllIntegrations] = useState(true);

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
      integration_ids: allIntegrations ? [ALL_INTEGRATIONS_SENTINEL] : selectedIds,
      workspace_path: workspacePath,
      loop_policy: {
        rollover_enabled: rolloverEnabled,
        idle_timeout_s: Number(idleTimeout) || 0,
      },
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
              <span className="kv"><b>已选</b> <code>{allIntegrations ? "全部" : selectedIds.length}</code> 个集成</span>
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
              <p className="card__lead">按集成实例选择；选中后该实例的全部 SDK 能力对 Actor 可见。</p>
              <label className="mb-3 flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={allIntegrations}
                  onChange={(e) => setAllIntegrations(e.target.checked)}
                  className="size-4 rounded border-input"
                />
                全部 integrations
              </label>
              <CapTree
                groups={capGroups}
                selectedIds={selectedIds}
                onChange={setSelectedIds}
                disabled={allIntegrations}
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
