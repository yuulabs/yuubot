import type { ActorResource } from "@/types/api";
import { Button } from "@/components/ui/button";
import { MessageSquare } from "lucide-react";
import { Link } from "@tanstack/react-router";

interface ActorActionsProps {
  actor: ActorResource;
}

export function ActorActions({ actor }: ActorActionsProps) {
  return (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="xs" asChild>
        <Link to="/chat" search={{ dialogId: actor.id }}>
          <MessageSquare size={14} className="mr-1" />
          Chat
        </Link>
      </Button>
    </div>
  );
}
