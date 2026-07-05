import { useState } from "react";

import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState, Page, Panel } from "@/shared/components";
import { requestBrowserNotificationPermission, subscribePushNotifications } from "@/shared/lib/api/notifications";
import { useBootstrap } from "@/shared/hooks";

export function SettingsPage() {
  const { data, error, isLoading } = useBootstrap();
  const [browserPermission, setBrowserPermission] = useState(
    typeof Notification !== "undefined" ? Notification.permission : "unsupported",
  );
  const [pushStatus, setPushStatus] = useState<string>("");
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;

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
    </Page>
  );
}
