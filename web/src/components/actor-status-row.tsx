import type { ActorResource } from "@/types/api";
import { StatusBadge } from "./status-badge";
import { Link } from "@tanstack/react-router";

interface ActorStatusRowProps {
  actor: ActorResource;
}

export function ActorStatusRow({ actor }: ActorStatusRowProps) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <Link
        to="/actors/$id"
        params={{ id: actor.id }}
        className="flex-1 truncate text-sm hover:underline"
      >
        {actor.name}
      </Link>
      <StatusBadge enabled={actor.enabled ?? false} />
    </div>
  );
}
