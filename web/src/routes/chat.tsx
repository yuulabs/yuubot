import { useEffect, useState } from "react";
import { createFileRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { Plus, MessageSquare, Search } from "lucide-react";
import { listConversations } from "@/lib/api";
import type { ConversationListItem } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export const Route = createFileRoute("/chat")({
  component: ChatPage,
});

function ChatPage() {
  const navigate = useNavigate();
  const { location } = useRouterState();
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  const fetchConversations = async () => {
    try {
      setLoading(true);
      const data = await listConversations();
      setConversations(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConversations();
  }, []);

  const filtered = search.trim()
    ? conversations.filter((conversation) =>
        conversation.conversation_id.toLowerCase().includes(search.toLowerCase()) ||
        conversation.actor_id.toLowerCase().includes(search.toLowerCase())
      )
    : conversations;

  const handleNewDialog = () => {
    const id = `dialog-${crypto.randomUUID()}`;
    navigate({ to: `/chat/${id}` });
  };

  if (location.pathname !== "/chat") {
    return <Outlet />;
  }

  return (
    <div className="flex h-full flex-col lg:flex-row">
      {/* Sidebar */}
      <aside className="w-full shrink-0 border-b p-4 lg:w-72 lg:border-b-0 lg:border-r flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">Dialogs</h2>
          <Button variant="ghost" size="icon" onClick={handleNewDialog} title="New Dialog">
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="relative mb-3">
          <Search className="absolute left-2 top-2.5 size-3 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search dialogs..."
            className="pl-7 h-8 text-xs"
          />
        </div>
        <div className="flex-1 overflow-auto space-y-1">
          {loading && <p className="text-xs text-muted-foreground p-2">Loading...</p>}
          {!loading && filtered.length === 0 && (
            <p className="text-xs text-muted-foreground p-2">No dialogs yet.</p>
          )}
        {filtered.map((d) => (
            <button
              key={d.conversation_id}
              onClick={() => navigate({ to: `/chat/${d.conversation_id}` })}
              className="w-full text-left p-2 rounded-md hover:bg-muted transition-colors"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium truncate">{d.conversation_id}</span>
                <span className="text-[10px] text-muted-foreground shrink-0 ml-2">
                  Chat
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground truncate mt-0.5">
                {d.updated_at ? `Updated ${formatConversationTime(d.updated_at)}` : d.actor_id}
              </p>
            </button>
          ))}
        </div>
      </aside>

      {/* Welcome area */}
      <section className="flex flex-1 items-center justify-center">
        <Card className="max-w-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <MessageSquare className="size-4" />
              Web Chat
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs text-muted-foreground">
            <p>Select a dialog from the sidebar or create a new one.</p>
            <Button variant="outline" size="sm" onClick={handleNewDialog} className="w-full">
              <Plus className="size-3 mr-1" /> New Dialog
            </Button>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

function formatConversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}
