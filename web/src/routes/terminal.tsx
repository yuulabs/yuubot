import { createFileRoute } from "@tanstack/react-router";

import { TerminalPage } from "@/features/terminal";

export const Route = createFileRoute("/terminal")({
  component: TerminalPage,
});
