import { createFileRoute } from "@tanstack/react-router";

import { CredentialsPage } from "@/features/credentials";

export const Route = createFileRoute("/credentials")({
  component: CredentialsPage,
});
