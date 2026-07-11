import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useState } from "react";

import {
  deleteActorKv,
  getActor,
  getActorKv,
  putActorKv,
  sendActorInbound,
  listActorSkills,
  setActorSkillLoaded,
} from "@/shared/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState, Page, ResourceCard, ResourceCardGrid, ResourceMeta, Status } from "@/shared/components";
import { useBootstrap, useConversations } from "@/shared/hooks";
import { formatModelSelector } from "./model-selector";

export function ActorDetailPage({ id }: { id: string }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname.endsWith("/edit")) {
    return <Outlet />;
  }
  const { data, error, isLoading } = useBootstrap();
  const conversationQuery = useConversations();
  const actorFromBootstrap = data?.actors.find((item) => item.id === id);
  const actorQuery = useQuery({
    queryKey: ["actor", id],
    queryFn: () => getActor(id),
    enabled: !isLoading && !error && !actorFromBootstrap,
  });
  const actor = actorFromBootstrap ?? actorQuery.data;
  const runtime = actorFromBootstrap;
  const skillsQuery = useQuery({ queryKey: ["actor-skills", id], queryFn: () => listActorSkills(id) });
  const skillMutation = useMutation({
    mutationFn: ({ skillId, loaded }: { skillId: string; loaded: boolean }) => setActorSkillLoaded(id, skillId, loaded),
    onSuccess: () => skillsQuery.refetch(),
  });

  if (isLoading || (!actor && actorQuery.isLoading)) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  if (actorQuery.isError) return <ErrorState error={actorQuery.error} />;
  if (!actor || !data) return <EmptyState>Actor not found.</EmptyState>;
  const routes = data?.routes.filter((route) => route.actor_id === id) ?? [];
  const conversations = conversationQuery.data?.filter((conversation) => conversation.actor_id === id) ?? [];
  return (
    <Page
      title={actor.name || actor.id}
      sub={actor.description || "Actor detail"}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button asChild><Link to="/admin/conversations/new" search={{ actor: id, prompt: "" }}>Start conversation</Link></Button>
          <Button variant="outline" asChild><a href={`/workspace?actor=${encodeURIComponent(id)}`} target="_blank" rel="noreferrer">Workspace</a></Button>
          <Button variant="outline" asChild><Link to="/actors/$id/edit" params={{ id }}>Edit</Link></Button>
        </div>
      }
    >
      <div className="grid gap-3">
        {runtime?.loaded_skills_warning && (
          <ResourceCard variant="actor" title="Skills need整理" subtitle={`当前加载 ${runtime.loaded_skill_count} 个 skills，建议不超过 ${runtime.max_loaded_skills_warning} 个`}>
            <Button variant="outline" asChild><Link to="/admin/conversations/new" search={{ actor: id, prompt: "请整理当前 Actor 的 skills：创建一个元 skill，列出下层 skill、适用场景和读取方式，并 ban 低频 skills。" }}>新建整理会话</Link></Button>
          </ResourceCard>
        )}
        <ResourceCard variant="actor" title="Workspace skills" subtitle={`${skillsQuery.data?.items.length ?? 0} installed for this Actor`}>
          <div className="grid gap-2">
            {skillsQuery.data?.items.map((skill) => <div key={skill.id} className="flex items-center justify-between gap-3"><span><strong>{skill.name}</strong> <small>{skill.description}</small></span><Button size="sm" variant="outline" disabled={skillMutation.isPending} onClick={() => skillMutation.mutate({ skillId: skill.id, loaded: !skill.loaded })}>{skill.loaded ? "Ban from prompt" : "Load in prompt"}</Button></div>)}
            {!skillsQuery.data?.items.length && <p className="page-sub">No workspace skills.</p>}
          </div>
        </ResourceCard>
        <ResourceCard
          variant="actor"
          label={actor.id}
          title="Actor overview"
          subtitle={actor.description || "This actor has no description yet."}
          status={<Status enabled={runtime?.enabled ?? true} label={runtime?.status ?? "loaded"} />}
        >
          <ResourceMeta
            items={[
              { label: "Model", value: formatModelSelector(actor.model), tone: actor.model ? "default" : "warning" },
              { label: "Workspace", value: actor.workspace },
            ]}
          />
          {runtime?.last_error && <pre className="resource-preview">{JSON.stringify(runtime.last_error, null, 2)}</pre>}
        </ResourceCard>
        <ActorInboundPanel actorId={id} />
        <ResourceCardGrid>
          <ActorKvPanel actorId={id} />
        </ResourceCardGrid>
        <ResourceCard
          variant="route"
          title="Routes"
          subtitle={`${routes.length} inbound bindings`}
        >
          {routes.length ? routes.map((route) => (
            <div key={route.id} className="resource-flow">
              <span className="resource-flow__node">{route.integration_type || "any"}</span>
              <span className="resource-flow__arrow">-&gt;</span>
              <span className="resource-flow__node">{route.pattern}</span>
              <span className="resource-flow__arrow">-&gt;</span>
              <span className="resource-flow__node">{actor.name || actor.id}</span>
            </div>
          )) : <p className="page-sub">No routes.</p>}
        </ResourceCard>
        <ResourceCard
          variant="conversation"
          title="Conversations"
          subtitle={`${conversations.length} saved threads`}
        >
          {conversations.length ? (
            <div className="resource-chip-row">
              {conversations.map((conversation) => (
                <Link key={conversation.id} className="resource-chip" to="/admin/conversations/$conversationId" params={{ conversationId: conversation.id }}>
                  {conversation.id}
                </Link>
              ))}
            </div>
          ) : <p className="page-sub">No conversations.</p>}
        </ResourceCard>
      </div>
    </Page>
  );
}

function ActorInboundPanel({ actorId }: { actorId: string }) {
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");
  const send = useMutation({
    mutationFn: () => sendActorInbound(actorId, { text }),
    onSuccess: (result) => setMessage(JSON.stringify(result)),
    onError: (err) => setMessage(err instanceof Error ? err.message : String(err)),
  });
  return (
    <ResourceCard variant="conversation" title="Inbound" subtitle="Deliver a message through the actor inbound endpoint.">
      <div className="grid gap-2">
        <textarea className="textarea" rows={3} value={text} onChange={(event) => setText(event.target.value)} />
        <div><Button size="sm" disabled={!text.trim() || send.isPending} onClick={() => send.mutate()}>Send inbound</Button></div>
        {message && <p className="page-sub">{message}</p>}
      </div>
    </ResourceCard>
  );
}

function ActorKvPanel({ actorId }: { actorId: string }) {
  const [key, setKey] = useState("");
  const [etag, setEtag] = useState<string | null>(null);
  const [valueText, setValueText] = useState("{}");
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const load = useMutation({
    mutationFn: () => getActorKv(actorId, key),
    onSuccess: (result) => {
      setEtag(result.etag);
      setValueText(JSON.stringify(result.data.value, null, 2));
      setMessage(`ETag ${result.etag ?? result.data.etag}`);
      setConflict(false);
    },
    onError: (err) => setMessage(err instanceof Error ? err.message : String(err)),
  });
  const save = useMutation({
    mutationFn: () => putActorKv(actorId, key, { value: JSON.parse(valueText) as unknown }, etag),
    onSuccess: (result) => {
      setEtag(result.etag);
      setMessage(`Saved ${result.etag ?? result.data.etag}`);
      setConflict(false);
    },
    onError: (err) => {
      const value = err instanceof Error ? err.message : String(err);
      setMessage(value);
      setConflict(value.startsWith("409"));
    },
  });
  const overwrite = useMutation({
    mutationFn: () => putActorKv(actorId, key, { value: JSON.parse(valueText) as unknown }, null),
    onSuccess: (result) => {
      setEtag(result.etag);
      setMessage(`Overwritten ${result.etag ?? result.data.etag}`);
      setConflict(false);
    },
    onError: (err) => setMessage(err instanceof Error ? err.message : String(err)),
  });
  const remove = useMutation({
    mutationFn: () => deleteActorKv(actorId, key),
    onSuccess: () => setMessage("Deleted"),
    onError: (err) => setMessage(err instanceof Error ? err.message : String(err)),
  });
  return (
    <ResourceCard variant="neutral" title="KV" subtitle="Load and edit actor-scoped key value documents.">
      <div className="grid gap-2">
        <input className="input" value={key} placeholder="key" onChange={(event) => setKey(event.target.value)} />
        <textarea className="textarea font-mono" rows={6} value={valueText} onChange={(event) => setValueText(event.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" disabled={!key || load.isPending} onClick={() => load.mutate()}>Load</Button>
          <Button size="sm" disabled={!key || save.isPending} onClick={() => save.mutate()}>Save</Button>
          <Button variant="outline" size="sm" disabled={!key || remove.isPending} onClick={() => remove.mutate()}>Delete</Button>
          {conflict && <Button variant="outline" size="sm" disabled={overwrite.isPending} onClick={() => overwrite.mutate()}>Overwrite</Button>}
        </div>
        {message && <p className="page-sub">{message}</p>}
      </div>
    </ResourceCard>
  );
}
