import { createFileRoute } from "@tanstack/react-router";
import { GatewayPage } from "@/features/gateway/gateway-page";

export const Route = createFileRoute("/gateway")({
  component: GatewayPage,
});
