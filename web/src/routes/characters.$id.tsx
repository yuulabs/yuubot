import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Trash2 } from "lucide-react";
import {
  useResourceList,
  useDeleteResource,
  useUpdateResource,
} from "@/hooks/use-resources";
import type { CharacterResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/characters/$id")({
  component: CharacterDetailPage,
});

function CharacterDetailPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");
  const deleteMutation = useDeleteResource("characters");
  const updateMutation = useUpdateResource<CharacterResource>("characters");

  const character = characters.find((c) => c.id === id);

  const [name, setName] = useState(character?.name ?? "");
  const [description, setDescription] = useState(character?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(character?.system_prompt ?? "");

  if (!character) {
    return (
      <div className="p-6">
        <Link to="/characters" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Back to characters
        </Link>
        <p className="text-muted-foreground">Character not found.</p>
      </div>
    );
  }

  const handleDelete = () => {
    if (confirm(`Delete character "${character.name}"?`)) {
      deleteMutation.mutate(character.id, {
        onSuccess: () => navigate({ to: "/characters" }),
      });
    }
  };

  const handleSave = async () => {
    await updateMutation.mutateAsync({
      id: character.id,
      data: {
        name,
        description,
        system_prompt: systemPrompt,
      },
    });
  };

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/characters">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="size-4" />
            </Button>
          </Link>
          <div>
            <h1 className="text-xl font-bold">{character.name}</h1>
            <p className="text-sm text-muted-foreground">Character ID: {character.id}</p>
          </div>
        </div>
        <Badge variant={character.is_builtin ? "secondary" : "outline"}>
          {character.is_builtin ? "builtin" : "custom"}
        </Badge>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {/* System prompt editor */}
          <Card>
            <CardHeader>
              <CardTitle>System Prompt</CardTitle>
            </CardHeader>
            <CardContent>
              <Textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={16}
                className="font-mono text-sm"
                placeholder="Enter the system prompt for this character..."
              />
            </CardContent>
          </Card>

          {/* Edit form */}
          <Card>
            <CardHeader>
              <CardTitle>Edit Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Name</label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Description</label>
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <Button
                onClick={handleSave}
                disabled={updateMutation.isPending}
              >
                {updateMutation.isPending ? "Saving..." : "Save Changes"}
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Sidebar info */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Details</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableBody>
                  <TableRow>
                    <TableCell className="font-medium">Name</TableCell>
                    <TableCell>{character.name}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">Facade</TableCell>
                    <TableCell><code>{character.facade_module}</code></TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">Language</TableCell>
                    <TableCell>{character.default_hints?.language ?? "—"}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">Tone</TableCell>
                    <TableCell>{character.default_hints?.tone || "—"}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">Builtin</TableCell>
                    <TableCell>{character.is_builtin ? "Yes" : "No"}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Danger Zone</CardTitle>
              <CardDescription>Irreversible actions</CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                variant="destructive"
                className="w-full"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="size-4" />
                Delete Character
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
