import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState, Page, Panel } from "@/shared/components";
import { applyUpdate, checkHealthz, getUpdateStatus, requestBrowserNotificationPermission, subscribePushNotifications } from "@/shared/lib/api";
import { useBootstrap } from "@/shared/hooks";

const updateQueryKey = ["update-status"] as const;
const updatePollTimeoutMs = 5 * 60 * 1000;

type UpdatePhase = "idle" | "waiting_offline" | "updating" | "reconnecting";

function shortCommit(commit?: string | null): string {
  if (!commit) return "unknown";
  return commit.slice(0, 7);
}

export function SettingsPage() {
  const { data, error, isLoading } = useBootstrap();
  const updateStatus = useQuery({
    queryKey: updateQueryKey,
    queryFn: getUpdateStatus,
    enabled: false,
    retry: false,
  });
  const [browserPermission, setBrowserPermission] = useState(
    typeof Notification !== "undefined" ? Notification.permission : "unsupported",
  );
  const [pushStatus, setPushStatus] = useState<string>("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [updatePhase, setUpdatePhase] = useState<UpdatePhase>("idle");
  const [updateMessage, setUpdateMessage] = useState("");
  const [updateLogPath, setUpdateLogPath] = useState<string | null>(null);
  const updateStartedAtRef = useRef<number | null>(null);

  const apply = useMutation({
    mutationFn: applyUpdate,
    onSuccess: (result) => {
      setConfirmOpen(false);
      setUpdateLogPath(result.log_path ?? null);
      updateStartedAtRef.current = Date.now();
      setUpdatePhase("waiting_offline");
      setUpdateMessage("Stopping server...");
    },
    onError: (applyError) => {
      setUpdateMessage(applyError instanceof Error ? applyError.message : "Update failed");
      setUpdatePhase("idle");
    },
  });

  useEffect(() => {
    if (updatePhase === "idle") return;

    let cancelled = false;
    const failUpdate = (message: string) => {
      setUpdatePhase("idle");
      setUpdateMessage(message);
      updateStartedAtRef.current = null;
    };
    const poll = async () => {
      const startedAt = updateStartedAtRef.current;
      if (startedAt !== null && Date.now() - startedAt >= updatePollTimeoutMs) {
        const logHint = updateLogPath ? ` See log: ${updateLogPath}` : "";
        failUpdate(`Update timed out waiting for the server to restart.${logHint}`);
        return;
      }

      const healthy = await checkHealthz().catch(() => false);
      if (cancelled) return;

      if (updatePhase === "waiting_offline") {
        if (!healthy) {
          setUpdatePhase("updating");
          setUpdateMessage("Updating from GitHub...");
        }
        return;
      }

      if (updatePhase === "updating" || updatePhase === "reconnecting") {
        if (healthy) {
          setUpdatePhase("reconnecting");
          setUpdateMessage("Server is back online. Reloading...");
          window.setTimeout(() => window.location.reload(), 500);
        } else if (updatePhase === "updating") {
          setUpdatePhase("reconnecting");
          setUpdateMessage("Waiting for server to restart...");
        }
      }
    };

    const timer = window.setInterval(() => {
      void poll();
    }, 1000);
    void poll();

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [updatePhase, updateLogPath]);

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;

  const status = updateStatus.data;
  const updating = updatePhase !== "idle";

  return (
    <Page title="Settings" sub="Control plane state, notifications, and loopback admin actions.">
      <div className="grid gap-3">
        <Panel>
          <h2 className="text-lg font-semibold">Admin State</h2>
          {data ? (
            <div className="grid gap-1 text-sm">
              <div>Schema version: {data.schema_version}</div>
              <div>Providers: {data.providers.length}</div>
              <div>Integrations: {data.integrations.length}</div>
              <div>Actors: {data.actors.length}</div>
              <div>Routes: {data.routes.length}</div>
            </div>
          ) : <EmptyState>No settings loaded.</EmptyState>}
        </Panel>

        <Panel>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">Software Update</h2>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={updateStatus.isFetching || updating}
                onClick={() => updateStatus.refetch()}
              >
                <RefreshCw className={updateStatus.isFetching ? "animate-spin" : ""} />
                <span>Check for updates</span>
              </Button>
              {status?.supported && status.update_available ? (
                <Button disabled={updating || apply.isPending} onClick={() => setConfirmOpen(true)}>
                  <Download />
                  <span>Update now</span>
                </Button>
              ) : null}
            </div>
          </div>
          <div className="grid gap-1 text-sm">
            <div>Install type: {status?.install_kind ?? "unknown"}</div>
            <div>Current version: {status?.current_version ?? "unknown"}</div>
            <div>Current commit: {shortCommit(status?.current_commit)}</div>
            <div>Remote commit: {shortCommit(status?.remote_commit)}</div>
            {status?.message ? <div>{status.message}</div> : null}
            {!status?.supported && status ? (
              <div className="text-muted-foreground">Updates are only available for git source installations.</div>
            ) : null}
            {updating ? <div className="font-medium">{updateMessage}</div> : null}
            {!updating && updateMessage ? <div className="text-destructive">{updateMessage}</div> : null}
            {updateStatus.error ? <ErrorState error={updateStatus.error} /> : null}
          </div>
        </Panel>

        <Panel>
          <h2 className="mb-2 text-lg font-semibold">Notifications</h2>
          <div className="grid gap-2 text-sm">
            <div>Browser permission: {browserPermission}</div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={async () => {
                  const permission = await requestBrowserNotificationPermission();
                  setBrowserPermission(permission);
                }}
              >
                Enable Browser Notifications
              </Button>
              <Button
                variant="outline"
                onClick={async () => {
                  const subscription = await subscribePushNotifications();
                  setPushStatus(subscription ? `Subscribed (${subscription.id})` : "Push subscription failed");
                }}
              >
                Enable Background Push
              </Button>
            </div>
            {pushStatus ? <div>{pushStatus}</div> : null}
          </div>
        </Panel>
        <Panel>
          <h2 className="text-lg font-semibold">Admin Actions</h2>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => fetch("/api/admin/interrupt", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true }) })}>
              Interrupt All
            </Button>
            <Button variant="outline" onClick={() => fetch("/api/admin/shutdown", { method: "POST" })}>
              Shutdown
            </Button>
          </div>
        </Panel>
      </div>

      <Dialog open={confirmOpen} onOpenChange={(open) => !apply.isPending && setConfirmOpen(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply update?</DialogTitle>
            <DialogDescription>
              The server will disconnect while yuubot pulls the latest code from GitHub, installs dependencies, migrates the database, and restarts. Active conversations may be interrupted.
              {data?.development ? " The Vite dev server is not restarted automatically; refresh it manually after the backend returns." : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={apply.isPending}>Cancel</Button>
            </DialogClose>
            <Button disabled={apply.isPending} onClick={() => apply.mutate()}>
              Apply update
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
