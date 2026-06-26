import { createFileRoute, Outlet, redirect, useRouterState } from "@tanstack/react-router";

export const Route = createFileRoute("/admin/conversations")({
  beforeLoad: ({ location }) => {
    if (location.pathname === "/admin/conversations") {
      throw redirect({ to: "/actors" });
    }
  },
  component: ConversationsRoute,
});

function ConversationsRoute() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname === "/admin/conversations") {
    return null;
  }
  return <Outlet />;
}
