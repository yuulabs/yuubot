import { createFileRoute } from "@tanstack/react-router";
import { Download, HardDrive, Upload } from "lucide-react";
import { useHealth } from "@/hooks/use-resources";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";
import { PageShell } from "@/components/baseline";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const { data: health } = useHealth();

  return (
    <PageShell title="Settings" sub="运行时信息、数据导入导出与插件管理。">
      <div className="view space-y-6">
        {/* Runtime info */}
      <Card>
        <CardHeader>
          <CardTitle>Runtime</CardTitle>
          <CardDescription>Current system status</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableBody>
              <TableRow>
                <TableCell className="font-medium">Admin</TableCell>
                <TableCell>{health?.admin ?? "unknown"}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Daemon</TableCell>
                <TableCell>{health?.daemon ?? "unknown"}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Plugins</TableCell>
                <TableCell>{health?.plugins ?? "unknown"}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Integrations</TableCell>
                <TableCell>{health?.integrations ?? "unknown"}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Ingress Rules</TableCell>
                <TableCell>{health?.ingress_rules ?? "unknown"}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Data management */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Upload className="size-4" />
              Export Data
            </CardTitle>
            <CardDescription>
              Download your configuration and data as an archive
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" className="w-full">
              <Download className="size-4" />
              Export Archive
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Download className="size-4" />
              Import Data
            </CardTitle>
            <CardDescription>
              Restore configuration from a previously exported archive
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" className="w-full">
              <Upload className="size-4" />
              Import Archive
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Plugin management */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive className="size-4" />
            Plugin Management
          </CardTitle>
          <CardDescription>
            Install and manage external integration plugins
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Plugin management interface will be available when the plugin system is initialized.
          </p>
        </CardContent>
      </Card>

      <Separator />

      {/* Danger zone */}
      <Card className="border-destructive/30">
        <CardHeader>
          <CardTitle className="text-destructive">Danger Zone</CardTitle>
          <CardDescription>
            Irreversible actions — proceed with caution
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border border-destructive/30 p-4">
            <div>
              <p className="text-sm font-medium">Reset all data</p>
              <p className="text-xs text-muted-foreground">
                Delete all resources, characters, actors, and configurations
              </p>
            </div>
            <Button variant="destructive" size="sm" disabled>
              Reset Data
            </Button>
          </div>
        </CardContent>
      </Card>
      </div>
    </PageShell>
  );
}
