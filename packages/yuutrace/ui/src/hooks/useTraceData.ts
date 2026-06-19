import { useCallback, useEffect, useRef, useState } from "react";
import type { Conversation, ConversationSummary } from "../types";

const PAGE_SIZE = 50;
const POLL_INTERVAL_MS = 3000;

type PageMode = "replace" | "append" | "poll";

interface UseTraceDataReturn {
  conversations: ConversationSummary[];
  selectedConversation: Conversation | null;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  selectConversation: (id: string) => void;
  loadMore: () => void;
  refresh: () => void;
}

/**
 * Data-fetching hook for the standalone TracePage.
 *
 * Calls `/api/conversations` and `/api/conversations/:id`.
 * Auto-polls every 3 seconds for real-time updates.
 * External consumers (e.g. yuuagents dashboard) should NOT use this hook —
 * they provide data directly via component props.
 */
export function useTraceData(baseUrl = "."): UseTraceDataReturn {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedConversation, setSelectedConversation] =
    useState<Conversation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const totalRef = useRef(0);
  const loadedRef = useRef(0);
  const loadingMore = useRef(false);
  const selectedIdRef = useRef<string | null>(null);
  const hasLoadedInitialPage = useRef(false);

  const mergeConversationPages = useCallback(
    (
      prev: ConversationSummary[],
      next: ConversationSummary[],
      mode: PageMode,
    ): ConversationSummary[] => {
      if (mode === "append") {
        const seen = new Set(prev.map((item) => item.id));
        return [...prev, ...next.filter((item) => !seen.has(item.id))];
      }

      if (mode === "replace" || prev.length === 0) {
        return next;
      }

      const prevById = new Map(prev.map((item) => [item.id, item]));
      const merged = next.map((item) => {
        const existing = prevById.get(item.id);
        return existing == null ? item : { ...existing, ...item };
      });
      const nextIds = new Set(next.map((item) => item.id));

      for (const item of prev) {
        if (!nextIds.has(item.id)) {
          merged.push(item);
        }
      }

      return merged;
    },
    [],
  );

  const mergeConversationDetail = useCallback(
    (prev: Conversation | null, next: Conversation): Conversation => {
      if (prev == null || prev.id !== next.id) {
        return next;
      }

      const prevSpans = new Map(prev.spans.map((span) => [span.span_id, span]));
      const mergedSpans = next.spans.map((span) => {
        const existing = prevSpans.get(span.span_id);
        if (existing == null) {
          return span;
        }

        const prevEvents = new Map(existing.events.map((event) => [event.id, event]));
        const mergedEvents = span.events.map((event) => {
          const existingEvent = prevEvents.get(event.id);
          return existingEvent == null ? event : { ...existingEvent, ...event };
        });

        return { ...existing, ...span, events: mergedEvents };
      });

      return { ...prev, ...next, spans: mergedSpans };
    },
    [],
  );

  const fetchPage = useCallback(
    async (offset: number, mode: PageMode) => {
      const shouldShowLoading =
        mode !== "poll" && (!hasLoadedInitialPage.current || mode !== "append");
      if (shouldShowLoading) {
        setLoading(true);
      }
      setError(null);
      try {
        const res = await fetch(
          `${baseUrl}/api/conversations?limit=${PAGE_SIZE}&offset=${offset}`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const page: ConversationSummary[] = data.conversations ?? [];
        totalRef.current = data.total ?? 0;
        loadedRef.current =
          mode === "append" ? loadedRef.current + page.length : page.length;
        hasLoadedInitialPage.current = true;
        setConversations((prev) => mergeConversationPages(prev, page, mode));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (shouldShowLoading) {
          setLoading(false);
        }
        loadingMore.current = false;
      }
    },
    [baseUrl, mergeConversationPages],
  );

  const fetchDetail = useCallback(
    async (id: string) => {
      try {
        const res = await fetch(`${baseUrl}/api/conversations/${id}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: Conversation = await res.json();
        setSelectedConversation((prev) => mergeConversationDetail(prev, data));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [baseUrl, mergeConversationDetail],
  );

  const loadMore = useCallback(() => {
    if (loadingMore.current) return;
    loadingMore.current = true;
    fetchPage(loadedRef.current, "append");
  }, [fetchPage]);

  const selectConversation = useCallback(
    async (id: string) => {
      selectedIdRef.current = id;
      setLoading(true);
      setError(null);
      await fetchDetail(id);
      setLoading(false);
    },
    [fetchDetail],
  );

  // Initial fetch
  useEffect(() => {
    fetchPage(0, "replace");
  }, [fetchPage]);

  // Auto-poll conversation list
  useEffect(() => {
    const id = setInterval(() => {
      fetchPage(0, "poll");
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchPage]);

  // Auto-poll selected conversation detail
  useEffect(() => {
    if (!selectedIdRef.current) return;
    const convId = selectedIdRef.current;
    const id = setInterval(() => {
      fetchDetail(convId);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [selectedConversation?.id, fetchDetail]);

  const hasMore = conversations.length < totalRef.current;

  return {
    conversations,
    selectedConversation,
    loading,
    error,
    hasMore,
    selectConversation,
    loadMore,
    refresh: () => fetchPage(0, "replace"),
  };
}
