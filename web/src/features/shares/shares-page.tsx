import { Outlet, useRouterState } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Check } from "lucide-react";

import { createShare, listShares, revokeShare } from "@/shared/lib/api";
import type { ShareGrant } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState, Page, Panel } from "@/shared/components";
import { useBootstrap } from "@/shared/hooks";

type Feedback = { title: string; body: string; copyText?: string };

export function SharesPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/shares") {
    return <Outlet />;
  }
  const { data: bootstrap } = useBootstrap();
  const queryClient = useQueryClient();
  const [actorId, setActorId] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const selectedActor = actorId || bootstrap?.actors[0]?.id || "";
  const shares = useQuery({ queryKey: ["shares"], queryFn: listShares });
  const publish = useMutation({
    mutationFn: () => createShare(selectedActor, sourcePath.trim()),
    onSuccess: (share) => {
      const url = shareUrl(share);
      setSourcePath("");
      setFeedback({ title: "Share created", body: url, copyText: url });
      void queryClient.invalidateQueries({ queryKey: ["shares"] });
    },
    onError: (err) => setFeedback({ title: "Publish failed", body: err instanceof Error ? err.message : String(err) }),
  });
  const remove = useMutation({
    mutationFn: async (ids: string[]) => {
      await Promise.all(ids.map((id) => revokeShare(id)));
      return ids;
    },
    onSuccess: (ids) => {
      setSelected((current) => {
        const next = new Set(current);
        for (const id of ids) {
          next.delete(id);
        }
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: ["shares"] });
    },
    onError: (err) => setFeedback({ title: "Delete failed", body: err instanceof Error ? err.message : String(err) }),
  });
  const shareItems = shares.data ?? [];
  const selectedIds = useMemo(() => Array.from(selected), [selected]);
  const allSelected = shareItems.length > 0 && selected.size === shareItems.length;

  useEffect(() => {
    if (!shares.data) {
      return;
    }
    const visibleIds = new Set(shareItems.map((share) => share.id));
    setSelected((current) => new Set(Array.from(current).filter((id) => visibleIds.has(id))));
  }, [shares.data, shareItems]);

  if (shares.isLoading) return <LoadingState />;
  if (shares.error) return <ErrorState error={shares.error} />;

  function toggle(shareId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(shareId)) {
        next.delete(shareId);
      } else {
        next.add(shareId);
      }
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(shareItems.map((share) => share.id)));
  }

  function deleteShares(ids: string[]) {
    if (!ids.length || !window.confirm(`Delete ${ids.length} share record(s) and published file(s)?`)) {
      return;
    }
    remove.mutate(ids);
  }

  async function copyShareUrl(key: string, url: string) {
    await copyToClipboard(url);
    setCopiedKey(key);
    window.setTimeout(() => {
      setCopiedKey((current) => current === key ? null : current);
    }, 1600);
  }

  return (
    <>
      <Page title="Shares" sub="Published workspace file grants exposed by /s/* public boundary.">
        <Panel>
          <div className="grid gap-2 md:grid-cols-[180px_1fr_auto]">
            <select className="input" value={selectedActor} onChange={(event) => setActorId(event.target.value)}>
              {bootstrap?.actors.map((actor) => <option key={actor.id} value={actor.id}>{actor.name || actor.id}</option>)}
            </select>
            <input className="input" placeholder="workspace relative path" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} />
            <Button disabled={!selectedActor || publish.isPending} onClick={() => publish.mutate()}>Publish</Button>
          </div>
        </Panel>
        {!shareItems.length ? (
          <EmptyState>No shares.</EmptyState>
        ) : (
          <Panel>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="page-sub">{selected.size} of {shareItems.length} selected</p>
              <Button variant="destructive" size="sm" disabled={!selected.size || remove.isPending} onClick={() => deleteShares(selectedIds)}>
                Delete selected
              </Button>
            </div>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>
                      <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all shares" />
                    </th>
                    <th>Source</th>
                    <th>Actor</th>
                    <th>Expires</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {shareItems.map((share) => {
                    const url = shareUrl(share);
                    return (
                      <tr key={share.id}>
                        <td>
                          <input type="checkbox" checked={selected.has(share.id)} onChange={() => toggle(share.id)} aria-label={`Select ${share.source_path}`} />
                        </td>
                        <td>
                          <div className="font-medium">{share.source_path}</div>
                          <a className="page-sub underline-offset-4 hover:underline" href={url} target="_blank" rel="noreferrer">
                            {url}
                          </a>
                        </td>
                        <td>{share.actor_id}</td>
                        <td>{formatShareDate(share.expires_at)}</td>
                        <td>{share.revoked ? "revoked" : "active"}</td>
                        <td>
                          <div className="flex flex-wrap gap-2">
                            <Button variant="outline" size="xs" onClick={() => void copyShareUrl(share.id, url)}>
                              {copiedKey === share.id ? <><Check size={12} /> Copied</> : "Copy"}
                            </Button>
                            <Button variant="destructive" size="xs" disabled={remove.isPending} onClick={() => deleteShares([share.id])}>
                              Delete
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </Page>
      <Dialog open={feedback !== null} onOpenChange={(open) => {
        if (!open) {
          setFeedback(null);
          setCopiedKey(null);
        }
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{feedback?.title ?? ""}</DialogTitle>
            <DialogDescription>{feedback?.body ?? ""}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {feedback?.copyText && (
              <Button variant="outline" onClick={() => void copyShareUrl("feedback", feedback.copyText ?? "")}>
                {copiedKey === "feedback" ? <><Check size={14} /> Copied</> : "Copy URL"}
              </Button>
            )}
            <DialogClose asChild>
              <Button>OK</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function formatShareDate(value: ShareGrant["expires_at"]): string {
  if (!value) {
    return "Never";
  }
  return new Date(value).toLocaleString();
}

function shareUrl(share: ShareGrant): string {
  return share.url ?? `/s/${share.id}`;
}

async function copyToClipboard(value: string): Promise<void> {
  await navigator.clipboard.writeText(value);
}
