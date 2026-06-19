import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useResourceList, useSetResourceEnabled } from "@/hooks/use-resources";
import type { IntegrationResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/integrations/$id")({
  component: IntegrationDetailPage,
});

function IntegrationDetailPage() {
  const { id } = Route.useParams();
  const { data: integrations = [] } =
    useResourceList<IntegrationResource>("integrations");
  const toggleMutation = useSetResourceEnabled("integrations");

  const integration = integrations.find((i) => i.id === id);

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
