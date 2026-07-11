import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

export function NewConversationLink({
  actorId,
  className,
  children,
}: {
  actorId: string;
  className?: string;
  children: ReactNode;
}) {
  if (!actorId) return null;
  return (
    <Link
      className={className}
      to="/admin/conversations/new"
      search={{ actor: actorId, prompt: "" }}
      title="New conversation"
      aria-label="New conversation"
    >
      {children}
    </Link>
  );
}
