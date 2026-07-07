import { createFileRoute } from "@tanstack/react-router";

import { McpServersPage } from "@/features/mcp-servers";

export const Route = createFileRoute("/mcp-servers")({
  component: McpServersPage,
});
