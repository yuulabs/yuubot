import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ArrowLeft, ExternalLink, Github } from "lucide-react";
import {
  useResourceList,
  useSetResourceEnabled,
  useUpdateResource,
} from "@/hooks/use-resources";
import type { IntegrationResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/integrations/$id")({
  component: IntegrationDetailPage,
});

function IntegrationDetailPage() {
  const { id } = Route.useParams();
  const { data: integrations = [] } =
    useResourceList<IntegrationResource>("integrations");
  const toggleMutation = useSetResourceEnabled("integrations");
  const updateMutation = useUpdateResource<IntegrationResource>("integrations");
  const [pat, setPat] = useState("");
  const [defaultOwner, setDefaultOwner] = useState("");
  const [defaultRepo, setDefaultRepo] = useState("");
  const [saveError, setSaveError] = useState("");

  const integration = integrations.find((i) => i.id === id);
  const isGitHub = integration?.name === "github";

  useEffect(() => {
    if (!isGitHub) return;
    setDefaultOwner(stringConfigValue(integration?.config?.default_owner));
    setDefaultRepo(stringConfigValue(integration?.config?.default_repo));
  }, [
    integration?.config?.default_owner,
    integration?.config?.default_repo,
    isGitHub,
  ]);

  if (!integration) {
    return (
      <div className="p-6">
        <Link to="/integrations" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Back to integrations
        </Link>
        <p className="text-muted-foreground">Integration not found.</p>
      </div>
    );
  }

  const handleToggle = () => {
    toggleMutation.mutate({
      id: integration.id,
      enabled: !integration.enabled,
    });
  };
  const handleGitHubSave = () => {
    const config = {
      ...configWithoutAccessToken(integration.config),
      default_owner: defaultOwner.trim(),
      default_repo: defaultRepo.trim(),
      ...(pat.trim() ? { access_token: pat.trim() } : {}),
    };
    setSaveError("");
    updateMutation.mutate(
      {
        id: integration.id,
        data: { ...integration, config },
      },
      {
        onSuccess: (updated) => {
          setPat("");
          setDefaultOwner(stringConfigValue(updated.config?.default_owner));
          setDefaultRepo(stringConfigValue(updated.config?.default_repo));
        },
        onError: (error) => {
          setSaveError(error instanceof Error ? error.message : "Save failed");
        },
      },
    );
  };
  const isGitHubConnected = isGitHub && hasSecretValue(integration.config?.access_token);

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/integrations">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="size-4" />
            </Button>
          </Link>
          <div>
            <h1 className="text-xl font-bold">{integration.name}</h1>
            <p className="text-sm text-muted-foreground">Integration ID: {integration.id}</p>
          </div>
        </div>
        <Badge variant={integration.enabled ? "default" : "secondary"}>
          {integration.enabled ? "enabled" : "disabled"}
        </Badge>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>Runtime settings for this integration</CardDescription>
          </CardHeader>
          <CardContent>
            {integration.config && Object.keys(integration.config).length > 0 ? (
              <Table>
                <TableBody>
                  {Object.entries(integration.config).map(([key, value]) => (
                    <TableRow key={key}>
                      <TableCell className="font-medium">{key}</TableCell>
                      <TableCell>
                        <code className="text-xs">
                          {typeof value === "object"
                            ? JSON.stringify(value)
                            : String(value)}
                        </code>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="text-sm text-muted-foreground">
                No configuration values set. This integration uses defaults.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {isGitHub ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-muted-foreground">GitHub authorization</span>
                  <Badge variant={isGitHubConnected ? "default" : "secondary"}>
                    {isGitHubConnected ? "connected" : "not connected"}
                  </Badge>
                </div>
                <div className="space-y-3 rounded-md border p-3 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium">Personal access token</p>
                      <p className="mt-1 text-muted-foreground">
                        Create a fine-grained token for the repositories yuubot
                        should access. Grant repository Issues read/write and
                        Contents read permissions.
                      </p>
                    </div>
                    <Button variant="ghost" size="icon-sm" asChild>
                      <a
                        href="https://github.com/settings/personal-access-tokens/new"
                        target="_blank"
                        rel="noreferrer"
                        aria-label="Create GitHub personal access token"
                      >
                        <ExternalLink className="size-4" />
                      </a>
                    </Button>
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-medium">Token</label>
                    <Input
                      type="password"
                      value={pat}
                      onChange={(event) => setPat(event.target.value)}
                      placeholder={
                        isGitHubConnected
                          ? "Leave blank to keep the current token"
                          : "Paste GitHub fine-grained PAT"
                      }
                    />
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Default owner</label>
                      <Input
                        value={defaultOwner}
                        onChange={(event) => setDefaultOwner(event.target.value)}
                        placeholder="Tomorrowdawn"
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Default repo</label>
                      <Input
                        value={defaultRepo}
                        onChange={(event) => setDefaultRepo(event.target.value)}
                        placeholder="opendawn"
                      />
                    </div>
                  </div>
                  {saveError ? (
                    <p className="text-xs text-destructive">{saveError}</p>
                  ) : null}
                  <Button
                    className="w-full"
                    onClick={handleGitHubSave}
                    disabled={updateMutation.isPending}
                  >
                    <Github className="mr-2 size-4" />
                    {updateMutation.isPending ? "Saving..." : "Save GitHub token"}
                  </Button>
                </div>
              </div>
            ) : null}
            <Table>
              <TableBody>
                <TableRow>
                  <TableCell className="font-medium">Name</TableCell>
                  <TableCell>{integration.name}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Status</TableCell>
                  <TableCell>
                    <Badge variant={integration.enabled ? "default" : "secondary"}>
                      {integration.enabled ? "enabled" : "disabled"}
                    </Badge>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
            <Button
              variant="outline"
              className="w-full"
              onClick={handleToggle}
              disabled={toggleMutation.isPending}
            >
              {integration.enabled ? "Disable" : "Enable"} Integration
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function hasSecretValue(value: unknown): boolean {
  if (value === "***") return true;
  if (value && typeof value === "object") return true;
  return typeof value === "string" && value.length > 0;
}

function stringConfigValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function configWithoutAccessToken(
  config: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const { access_token: _accessToken, ...rest } = config ?? {};
  return rest;
}
