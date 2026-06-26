// capability-sets.tsx — `/capability-sets` browse view (ISSUE-0007 S5).
//
// Demo-aligned rewrite of the demo `view--capability-sets`: page-head +
// `.crud-main` (CrudHeader with count + `.table-wrap` data table, Empty when
// the list is empty). The right-side fixed create form is removed; the
// "新建 Capability Set" action is pushed into the shell topbar via
// `useAppShellActions` (the shell owns the topbar; this route owns only the
// page body), matching the S2 app-shell reconciling note.
import { useEffect } from "react";
import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { Pencil, Trash2 } from "lucide-react";
import {
  useDeleteResource,
  useResourceList,
} from "@/hooks/use-resources";
import type { CapabilitySetResource } from "@/types/api";
import {
  Button,
} from "@/components/ui/button";
import {
  CrudHeader,
  Empty,
  PageShell,
  StatusPill,
  useAppShellActions,
} from "@/components/baseline";
import { DataTable } from "@/components/data-table";

export const Route = createFileRoute("/capability-sets")({
  component: CapabilitySetsPage,
});

function CapabilitySetsPage() {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { setActions } = useAppShellActions();
  const { data: capabilitySets = [], isLoading, error } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const deleteMutation = useDeleteResource("capability-sets");

  // Child routes (/capability-sets/new, /capability-sets/$id/edit) render here.
  const isBrowse = pathname === "/capability-sets";
  const goNew = () => navigate({ to: "/capability-sets/new" });

  // Push the primary "新建 Capability Set" action into the shell topbar when
  // we are on the browse view; clear it otherwise (child routes own their
  // own actions). Run unconditionally so hook order stays stable across
  // parent ↔ child route transitions.
  useEffect(() => {
    if (!isBrowse) return;
    setActions(
      <Button onClick={goNew} className="btn btn--primary">
        新建 Capability Set
      </Button>,
    );
    return () => setActions(null);
  }, [setActions, isBrowse]);

  if (!isBrowse) {
    return <Outlet />;
  }

  if (isLoading) {
    return <PageShell title="Capability Sets">加载中…</PageShell>;
  }
  if (error) {
    return <PageShell title="Capability Sets">出错：{error.message}</PageShell>;
  }

  const handleDelete = (id: string) => {
    if (confirm("删除这个 Capability Set？")) deleteMutation.mutate(id);
  };

  return (
    <PageShell
      title="Capability Sets"
      sub="把一组可调用的能力（工具 / 函数）打包成一个集合，Actor 引用一个集合即获得全部能力。能力来自各集成与内置工具。"
    >
      <div className="crud-main">
        <CrudHeader title="集合" count={capabilitySets.length} />

        {capabilitySets.length === 0 ? (
          <Empty
            illustration="Set"
            title="还没有 Capability Set"
            description="点击右上「新建 Capability Set」创建一个，然后在 Actor 编辑器里绑定它。"
            action={
              <Button onClick={goNew} className="btn btn--primary">
                新建 Capability Set
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={[
              {
                key: "name",
                label: "名称",
                render: (cs) => <span className="font-medium">{cs.name}</span>,
              },
              {
                key: "desc",
                label: "描述",
                render: (cs) => cs.description || "—",
              },
              {
                key: "caps",
                label: "能力",
                render: (cs) => cs.integration_ids.length,
              },
              {
                key: "ws",
                label: "工作区",
                render: (cs) => cs.workspace_path || "—",
              },
              {
                key: "tools",
                label: "工具",
                render: (cs) => cs.tools.length,
              },
              {
                key: "rollover",
                label: "历史压缩",
                render: (cs) => (
                  <StatusPill
                    variant={cs.loop_policy.rollover_enabled ? "running" : "default"}
                  >
                    {cs.loop_policy.rollover_enabled ? "已启用" : "已关闭"}
                  </StatusPill>
                ),
              },
              {
                key: "actions",
                label: "",
                render: (cs) => (
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="icon" asChild>
                      <Link to="/capability-sets/$id/edit" params={{ id: cs.id }}>
                        <Pencil className="size-3.5" />
                      </Link>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleDelete(cs.id)}
                      disabled={deleteMutation.isPending}
                    >
                      <Trash2 className="size-3.5 text-destructive" />
                    </Button>
                  </div>
                ),
              },
            ]}
            rows={capabilitySets}
          />
        )}
      </div>
    </PageShell>
  );
}
