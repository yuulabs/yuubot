import { Link } from "@tanstack/react-router";
import { RefreshCw, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useRefreshBootstrap } from "@/shared/hooks";

export function RefreshButton() {
  const refresh = useRefreshBootstrap();
  return (
    <Button type="button" variant="outline" onClick={refresh}>
      <RefreshCw size={16} />
      <span>Refresh</span>
    </Button>
  );
}

export function DeleteButton({ onDelete }: { onDelete: () => void }) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => {
        if (window.confirm("Delete this record?")) {
          onDelete();
        }
      }}
    >
      <Trash2 size={14} />
      <span>Delete</span>
    </Button>
  );
}

export function DetailLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="font-medium underline-offset-4 hover:underline">
      {children}
    </Link>
  );
}
