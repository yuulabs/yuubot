import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ArrowLeft, ExternalLink, Github, Globe } from "lucide-react";
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
  const [tavilyKey, setTavilyKey] = useState("");
  const [tavilyBaseUrl, setTavilyBaseUrl] = useState("");
  const [webTimeout, setWebTimeout] = useState("");
  const [webUserAgent, setWebUserAgent] = useState("");
  const [webMaxReadBytes, setWebMaxReadBytes] = useState("");
  const [webMaxReadChars, setWebMaxReadChars] = useState("");
  const [webMaxDownloadBytes, setWebMaxDownloadBytes] = useState("");
  const [saveError, setSaveError] = useState("");
  const [runtimeError, setRuntimeError] = useState("");

  const integration = integrations.find((i) => i.id === id);
  const isGitHub = integration?.name === "github";
  const isWeb = integration?.name === "web";

  useEffect(() => {
    if (!isGitHub) return;
    setDefaultOwner(stringConfigValue(integration?.config?.default_owner));
    setDefaultRepo(stringConfigValue(integration?.config?.default_repo));
  }, [
    integration?.config?.default_owner,
    integration?.config?.default_repo,
    isGitHub,
  ]);

  useEffect(() => {
    if (!isWeb) return;
    setTavilyBaseUrl(
      stringConfigValue(integration?.config?.tavily_base_url) ||
        "https://api.tavily.com",
    );
    setWebTimeout(numberConfigValue(integration?.config?.timeout_s, 30));
    setWebUserAgent(
      stringConfigValue(integration?.config?.user_agent) || "yuubot/0.1",
    );
    setWebMaxReadBytes(
      numberConfigValue(integration?.config?.max_read_bytes, 2_000_000),
    );
    setWebMaxReadChars(
      numberConfigValue(integration?.config?.max_read_chars, 80_000),
    );
    setWebMaxDownloadBytes(
      numberConfigValue(integration?.config?.max_download_bytes, 10_000_000),
    );
  }, [
    integration?.config?.max_download_bytes,
    integration?.config?.max_read_bytes,
    integration?.config?.max_read_chars,
    integration?.config?.tavily_base_url,
    integration?.config?.timeout_s,
    integration?.config?.user_agent,
    isWeb,
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
    setRuntimeError("");
    toggleMutation.mutate(
      {
        id: integration.id,
        enabled: !integration.enabled,
      },
      {
        onError: (error) => {
          const message =
            error instanceof Error ? error.message : "Runtime update failed";
          setRuntimeError(
            isGitHub && !isGitHubConnected
              ? `Enable failed. Save a GitHub personal access token before enabling. ${message}`
              : message,
          );
        },
      },
    );
  };
  const handleGitHubSave = () => {
    const config = {
      ...configWithoutAccessToken(integration.config),
      default_owner: defaultOwner.trim(),
      default_repo: defaultRepo.trim(),
      ...(pat.trim() ? { access_token: pat.trim() } : {}),
    };
    setSaveError("");
    const patch: Pick<IntegrationResource, "config"> = { config };
    updateMutation.mutate(
      {
        id: integration.id,
        data: patch,
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

  const handleWebSave = () => {
    const config = {
      ...configWithoutSecret(integration.config, "api_key"),
      tavily_base_url: tavilyBaseUrl.trim() || "https://api.tavily.com",
      timeout_s: numericInputValue(webTimeout, 30),
      user_agent: webUserAgent.trim() || "yuubot/0.1",
      max_read_bytes: numericInputValue(webMaxReadBytes, 2_000_000),
      max_read_chars: numericInputValue(webMaxReadChars, 80_000),
      max_download_bytes: numericInputValue(webMaxDownloadBytes, 10_000_000),
      ...(tavilyKey.trim() ? { api_key: tavilyKey.trim() } : {}),
    };
    setSaveError("");
    const patch: Pick<IntegrationResource, "config"> = { config };
    updateMutation.mutate(
      {
        id: integration.id,
        data: patch,
      },
      {
        onSuccess: (updated) => {
          setTavilyKey("");
          setTavilyBaseUrl(
            stringConfigValue(updated.config?.tavily_base_url) ||
              "https://api.tavily.com",
          );
          setWebTimeout(numberConfigValue(updated.config?.timeout_s, 30));
          setWebUserAgent(
            stringConfigValue(updated.config?.user_agent) || "yuubot/0.1",
          );
          setWebMaxReadBytes(
            numberConfigValue(updated.config?.max_read_bytes, 2_000_000),
          );
          setWebMaxReadChars(
            numberConfigValue(updated.config?.max_read_chars, 80_000),
          );
          setWebMaxDownloadBytes(
            numberConfigValue(updated.config?.max_download_bytes, 10_000_000),
          );
        },
        onError: (error) => {
          setSaveError(error instanceof Error ? error.message : "Save failed");
        },
      },
    );
  };
  const isGitHubConnected = isGitHub && hasSecretValue(integration.config?.access_token);
  const isWebConnected = isWeb && hasSecretValue(integration.config?.api_key);

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

            {isWeb ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-muted-foreground">Tavily authorization</span>
                  <Badge variant={isWebConnected ? "default" : "secondary"}>
                    {isWebConnected ? "connected" : "not connected"}
                  </Badge>
                </div>
                <div className="space-y-3 rounded-md border p-3 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium">Tavily API key</p>
                      <p className="mt-1 text-muted-foreground">
                        Used by web.search. web.read and web.download use the
                        fetch settings below.
                      </p>
                    </div>
                    <Button variant="ghost" size="icon-sm" asChild>
                      <a
                        href="https://app.tavily.com/home"
                        target="_blank"
                        rel="noreferrer"
                        aria-label="Open Tavily dashboard"
                      >
                        <ExternalLink className="size-4" />
                      </a>
                    </Button>
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-medium">API key</label>
                    <Input
                      type="password"
                      value={tavilyKey}
                      onChange={(event) => setTavilyKey(event.target.value)}
                      placeholder={
                        isWebConnected
                          ? "Leave blank to keep the current key"
                          : "Paste Tavily API key"
                      }
                    />
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Tavily base URL</label>
                      <Input
                        value={tavilyBaseUrl}
                        onChange={(event) => setTavilyBaseUrl(event.target.value)}
                        placeholder="https://api.tavily.com"
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Timeout seconds</label>
                      <Input
                        type="number"
                        min="1"
                        value={webTimeout}
                        onChange={(event) => setWebTimeout(event.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-medium">User agent</label>
                    <Input
                      value={webUserAgent}
                      onChange={(event) => setWebUserAgent(event.target.value)}
                      placeholder="yuubot/0.1"
                    />
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Read bytes</label>
                      <Input
                        type="number"
                        min="1"
                        value={webMaxReadBytes}
                        onChange={(event) => setWebMaxReadBytes(event.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Read chars</label>
                      <Input
                        type="number"
                        min="1"
                        value={webMaxReadChars}
                        onChange={(event) => setWebMaxReadChars(event.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Download bytes</label>
                      <Input
                        type="number"
                        min="1"
                        value={webMaxDownloadBytes}
                        onChange={(event) => setWebMaxDownloadBytes(event.target.value)}
                      />
                    </div>
                  </div>
                  {saveError ? (
                    <p className="text-xs text-destructive">{saveError}</p>
                  ) : null}
                  <Button
                    className="w-full"
                    onClick={handleWebSave}
                    disabled={updateMutation.isPending}
                  >
                    <Globe className="mr-2 size-4" />
                    {updateMutation.isPending ? "Saving..." : "Save web settings"}
                  </Button>
                </div>
              </div>
            ) : null}

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
            <CardTitle>Runtime</CardTitle>
            <CardDescription>Controls whether the daemon runs this record</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Table>
              <TableBody>
                <TableRow>
                  <TableCell className="font-medium">ID</TableCell>
                  <TableCell>{integration.id}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Kind</TableCell>
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
            {runtimeError ? (
              <p className="text-xs text-destructive">{runtimeError}</p>
            ) : null}
            <Button
              variant={integration.enabled ? "outline" : "default"}
              className="w-full"
              onClick={handleToggle}
              disabled={toggleMutation.isPending}
            >
              {toggleMutation.isPending
                ? "Updating..."
                : `${integration.enabled ? "Disable" : "Enable"} Integration`}
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

function numberConfigValue(value: unknown, fallback: number): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : String(fallback);
}

function numericInputValue(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function configWithoutAccessToken(
  config: Record<string, unknown> | undefined,
): Record<string, unknown> {
  return configWithoutSecret(config, "access_token");
}

function configWithoutSecret(
  config: Record<string, unknown> | undefined,
  key: string,
): Record<string, unknown> {
  const rest = { ...(config ?? {}) };
  delete rest[key];
  return rest;
}
