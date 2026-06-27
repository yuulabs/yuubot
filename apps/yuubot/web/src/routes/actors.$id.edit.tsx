import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/actors/$id/edit")({
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/actors/$id", params: { id: params.id } });
  },
});
