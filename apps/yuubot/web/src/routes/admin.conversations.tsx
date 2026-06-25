import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

// ISSUE-0010: "start a conversation with an Actor" is the sole creation
// path. The top-level Conversation list page, its top-level New-conversation
// creator, and the welcome card are gone. Conversations are reached only
// from an Actor (row action on /actors + Actor detail page history list).
//
// This is now a layout-only shell: deeper paths (the $conversationId child)
// render through <Outlet/>. A bare /admin/conversations visit — the dead
// management surface — redirects to /actors.
export const Route = createFileRoute("/admin/conversations")({
  beforeLoad: ({ location }) => {
    if (location.pathname === "/admin/conversations") {
      throw redirect({ to: "/actors" });
    }
  },
  component: () => <Outlet />,
});
