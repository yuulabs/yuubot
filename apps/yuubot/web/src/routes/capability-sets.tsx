import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Layers, Trash2 } from "lucide-react";
import {
  useResourceList,
  useCreateResource,
  useDeleteResource,
  useLiveCapabilities,
} from "@/hooks/use-resources";
import type { CapabilitySetResource } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";


export const Route = createFileRoute("/capability-sets")({
  component: CapabilitySetsPage,
});

interface CapabilitySetFormData {
  name: string;
  description: string;
  capabilityIds: string[];
  workspacePath: string;
  memoryEnabled: boolean;
  budget: string;
}

const defaultForm: CapabilitySetFormData = {
  name: "",
  description: "",
  capabilityIds: [],
  workspacePath: "",
  memoryEnabled: false,
  budget: "",
};

function CapabilitySetsPage() {
  const { data: capabilitySets = [], isLoading, error } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const { data: liveCapabilities = [] } = useLiveCapabilities();
  const createMutation = useCreateResource<CapabilitySetResource>("capability-sets");
  const deleteMutation = useDeleteResource("capability-sets");

  const [form, setForm] = useState<CapabilitySetFormData>(defaultForm);

  const capabilityOptions = liveCapabilities
    .map((cap) => ({
      id: cap.capability_id,
      name: cap.capability_name || cap.capability_id,
      description: cap.description,
      integrationName: cap.integration_name,
      enabled: cap.enabled,
    }))
    .sort((a, b) => a.id.localeCompare(b.id));

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({
      name: form.name,
      description: form.description,
      integration_capability_ids: form.capabilityIds,
      workspace_path: form.workspacePath,
      runtime_policy: { memory_enabled: form.memoryEnabled },
      resource_policy: { budget_usd_daily: Number(form.budget) || null },
    });
    setForm(defaultForm);
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this capability set?")) deleteMutation.mutate(id);
  };

  if (isLoading) return <PageShell>Loading capability sets...</PageShell>;
  if (error) return <PageShell>Error: {error.message}</PageShell>;

  return (
    <PageShell>
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Table */}
        <Card className="flex-1">
          <CardHeader>
            <CardTitle>Capability Sets</CardTitle>
            <CardDescription>
              {capabilitySets.length} capability sets configured
            </CardDescription>
          </CardHeader>
          <CardContent>
            {capabilitySets.length === 0 ? (
              <Empty text="No capability sets yet" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead>Capabilities</TableHead>
                    <TableHead>Workspace</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {capabilitySets.map((cs) => (
                    <TableRow key={cs.id}>
                      <TableCell className="font-medium">{cs.name}</TableCell>
                      <TableCell className="max-w-xs truncate text-sm">
                        {cs.description || "—"}
                      </TableCell>
                      <TableCell className="text-sm">
                        {cs.integration_capability_ids.length}
                      </TableCell>
                      <TableCell className="text-sm">
                        {cs.workspace_path || "—"}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDelete(cs.id)}
                          disabled={deleteMutation.isPending}
                        >
                          <Trash2 className="size-3.5 text-destructive" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Creation form */}
        <Card className="w-full lg:w-80">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Layers className="size-4" />
              Create Capability Set
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4">
              <FormField label="Name" required>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </FormField>
              <FormField label="Description">
                <Input
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
              </FormField>
              <FormField label="Workspace Path">
                <Input
                  value={form.workspacePath}
                  onChange={(e) => setForm({ ...form, workspacePath: e.target.value })}
                  placeholder="test/"
                />
              </FormField>
              <FormField label="Daily Budget (USD)">
                <Input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.budget}
                  onChange={(e) => setForm({ ...form, budget: e.target.value })}
                  placeholder="0 = unlimited"
                />
              </FormField>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.memoryEnabled}
                  onChange={(e) =>
                    setForm({ ...form, memoryEnabled: e.target.checked })
                  }
                  className="size-4 rounded border-input"
                />
                Memory enabled
              </label>
              <FormField label="Capabilities">
                {capabilityOptions.length > 0 ? (
                  <div className="max-h-36 space-y-2 overflow-auto rounded-md border p-2">
                    {capabilityOptions.map((capability) => (
                      <label
                        key={capability.id}
                        className="flex items-start gap-2 text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={form.capabilityIds.includes(capability.id)}
                          onChange={(e) =>
                            setForm({
                              ...form,
                              capabilityIds: toggleCapabilityId(
                                form.capabilityIds,
                                capability.id,
                                e.target.checked,
                              ),
                            })
                          }
                          className="mt-0.5 size-4 rounded border-input"
                        />
                        <span className="min-w-0">
                          <span className="block font-medium">
                            {capability.name}
                            {!capability.enabled && (
                              <span className="ml-1.5 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                                disabled
                              </span>
                            )}
                          </span>
                          <span className="block break-all text-xs text-muted-foreground">
                            {capability.id}
                          </span>
                          <span className="block text-xs text-muted-foreground">
                            via {capability.integrationName}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No capabilities available. Create an integration first to enable capability selection.
                  </p>
                )}
              </FormField>
              {createMutation.error && (
                <p className="text-xs text-destructive">
                  {createMutation.error.message}
                </p>
              )}
              <Button
                type="submit"
                className="w-full"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Creating..." : "Create Capability Set"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="p-6">{children}</div>;
}

function FormField({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium">
        {label}
        {required && <span className="ml-0.5 text-destructive">*</span>}
      </label>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}

function toggleCapabilityId(
  ids: string[],
  id: string,
  checked: boolean,
): string[] {
  if (checked) {
    return Array.from(new Set([...ids, id])).sort();
  }
  return ids.filter((value) => value !== id);
}
