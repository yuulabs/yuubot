import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Wand2, Trash2 } from "lucide-react";
import { useResourceList, useCreateResource, useDeleteResource } from "@/hooks/use-resources";
import type { CharacterResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/characters")({
  component: CharactersPage,
});

interface CharacterFormData {
  name: string;
  description: string;
  systemPrompt: string;
}

const defaultForm: CharacterFormData = {
  name: "",
  description: "",
  systemPrompt: "",
};

function CharactersPage() {
  const { data: characters = [], isLoading, error } = useResourceList<CharacterResource>("characters");
  const createMutation = useCreateResource<CharacterResource>("characters");
  const deleteMutation = useDeleteResource("characters");

  const [form, setForm] = useState<CharacterFormData>(defaultForm);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({
      name: form.name,
      description: form.description,
      system_prompt: form.systemPrompt,
      facade_module: "yb",
      default_hints: { language: "zh-CN", tone: "" },
      is_builtin: false,
      builtin_version: "",
      cloned_from: "",
    });
    setForm(defaultForm);
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this character?")) deleteMutation.mutate(id);
  };

  const handleClone = (character: CharacterResource) => {
    setForm({
      name: `${character.name}-copy`,
      description: character.description,
      systemPrompt: character.system_prompt,
    });
  };

  if (isLoading) return <PageShell>Loading characters...</PageShell>;
  if (error) return <PageShell>Error: {error.message}</PageShell>;

  return (
    <PageShell>
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Table */}
        <Card className="flex-1">
          <CardHeader>
            <CardTitle>Characters</CardTitle>
            <CardDescription>{characters.length} characters defined</CardDescription>
          </CardHeader>
          <CardContent>
            {characters.length === 0 ? (
              <Empty text="No characters yet" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead>Facade</TableHead>
                    <TableHead>Builtin</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {characters.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="font-medium">
                        <Link
                          to="/characters/$id"
                          params={{ id: c.id }}
                          className="hover:underline"
                        >
                          {c.name}
                        </Link>
                      </TableCell>
                      <TableCell className="max-w-xs truncate text-sm">
                        {c.description}
                      </TableCell>
                      <TableCell>
                        <code className="text-xs">{c.facade_module}</code>
                      </TableCell>
                      <TableCell>
                        <Badge variant={c.is_builtin ? "secondary" : "outline"}>
                          {c.is_builtin ? "builtin" : "custom"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Link to="/characters/$id" params={{ id: c.id }}>
                            <Button variant="ghost" size="xs">Edit</Button>
                          </Link>
                          <Button
                            variant="ghost"
                            size="xs"
                            onClick={() => handleClone(c)}
                          >
                            Clone
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDelete(c.id)}
                            disabled={deleteMutation.isPending}
                          >
                            <Trash2 className="size-3.5 text-destructive" />
                          </Button>
                        </div>
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
              <Wand2 className="size-4" />
              Clone Or Create
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Clone from</label>
                <select
                  value=""
                  onChange={(e) => {
                    if (!e.target.value) return;
                    const c = characters.find((ch) => ch.id === e.target.value);
                    if (c) handleClone(c);
                  }}
                  className="h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                >
                  <option value="">Blank character</option>
                  {characters.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">
                  Name<span className="ml-0.5 text-destructive">*</span>
                </label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Description</label>
                <Input
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">
                  System Prompt<span className="ml-0.5 text-destructive">*</span>
                </label>
                <Textarea
                  value={form.systemPrompt}
                  onChange={(e) => setForm({ ...form, systemPrompt: e.target.value })}
                  rows={8}
                  required
                />
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Saving..." : "Save Character"}
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

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}
