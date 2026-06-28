// conversation-entry-via-actor.test.ts
//
// Source-marker test for ISSUE-0010: "start a conversation with this Actor"
// is the sole creation path. Asserts the four route files + actor-actions
// reflect the per-Actor conversation entry, and that the top-level
// [New Conversation] creator is gone and the bare parent route redirects away.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (name: string): string =>
  readFileSync(path.join(here, name), "utf8");

const rootSrc = read("__root.tsx");
const listSrc = read("admin.conversations.tsx");
const convoSrc = read("admin.conversations.$conversationId.tsx");
const actorsListSrc = read("actors.tsx");
const actorDetailSrc = read("actors.$id.tsx");

test("__root.tsx no longer exposes a top-level Conversation nav entry", () => {
  assert.ok(!rootSrc.includes('label: "Admin Conversation"'),
    "top-level 'Admin Conversation' nav label must be removed");
  assert.ok(!/to:\s*"\/admin\/conversations"/.test(rootSrc),
    "no nav item should target /admin/conversations");
});

test("admin.conversations.tsx remains only a parent route without top-level UI", () => {
  assert.ok(!listSrc.includes("handleNewConversation"),
    "handleNewConversation creator must be removed");
  assert.ok(!listSrc.includes("New Conversation"),
    "[New Conversation] button copy must be removed");
  assert.ok(!listSrc.includes("listConversations"),
    "bare /admin/conversations must not render a top-level history list");
  assert.ok(listSrc.includes("redirect") && listSrc.includes("/actors"),
    "bare /admin/conversations should redirect users back to Actors");
});

test("admin.conversations.$conversationId.tsx detects the actor-bound draft and locks the actor", () => {
  assert.ok(convoSrc.includes("actor-"),
    "must derive the actor draft from the `actor-` prefix of conversationId");
  assert.ok(/isDraft\s*=/.test(convoSrc) && /actor-/.test(convoSrc),
    "isDraft predicate must cover the actor- case");
  // The BindingPanel must no longer offer an editable Select for actor
  // selection in the actor-bound-draft path: the Select is conditionally
  // removed (not rendered when actorLocked) so a locked draft renders a
  // read-only Badge instead.
  assert.ok(/actorLocked\s*\?|actorLocked\s*&&|\{actorLocked\s*\?|!actorLocked/.test(convoSrc),
    "an actorLocked predicate must gate the editable Select");
  const selectMatches = convoSrc.match(/<Select[\s>]/g) || [];
  assert.ok(selectMatches.length <= 1,
    "the actor-selection Select must be removed or at most one Select remains");
});

test("admin.conversations.$conversationId.tsx filters history rail by the active actor", () => {
  assert.ok(
    /\.filter\(\s*\(item\)\s*=>\s*item\.actor_id\s*===\s*actorId\s*\)/.test(convoSrc),
    "conversation history rail must only show rows for the active actor",
  );
});

test("admin.conversations.$conversationId.tsx exposes an actor-scoped quick new conversation action", () => {
  assert.ok(convoSrc.includes("chat__quick-new"),
    "conversation page must render the fixed quick-new action");
  assert.ok(convoSrc.includes('aria-label="新对话"'),
    "quick-new action must be accessible by label");
  assert.ok(convoSrc.includes("newConversationId") && convoSrc.includes("`actor-${actorId}`"),
    "quick-new action must route to the actor-bound draft");
});

test("admin.conversations.$conversationId.tsx only auto-scrolls while near the bottom", () => {
  assert.ok(convoSrc.includes("shouldAutoScrollRef"),
    "conversation page must track whether auto-scroll should remain enabled");
  assert.ok(convoSrc.includes("distanceFromBottom"),
    "scroll handler must compute the user's distance from the bottom");
  assert.ok(convoSrc.includes("distanceFromBottom < 96"),
    "auto-scroll should be disabled once the user scrolls away from the bottom");
  assert.ok(convoSrc.includes("!shouldAutoScrollRef.current"),
    "display item updates must not force-scroll while the user is reading history");
});

test("actors.tsx row action links to the actor-bound draft route", () => {
  assert.ok(actorsListSrc.includes("/admin/conversations/actor-${actor.id}") ||
    actorsListSrc.includes("`actor-${actor.id}`"),
    "actors list must link to /admin/conversations/actor-<actor.id>");
  // Workspace column regression (must remain intact).
  assert.ok(actorsListSrc.includes("capability_set?.workspace_path") ||
    actorsListSrc.includes("capability_set.workspace_path"),
    "Workspace column must still read capability_set.workspace_path");
});

test("actors.$id.tsx lists this Actor's historical conversations filtered by actor_id", () => {
  assert.ok(actorDetailSrc.includes("listConversations"),
    "Actor detail page must import listConversations");
  assert.ok(/\.filter\(\s*\(c\)\s*=>\s*c\.actor_id\s*===\s*(?:actor\.id|id)/.test(actorDetailSrc) ||
    /actor_id\s*===\s*actor\.id/.test(actorDetailSrc),
    "conversations must be filtered by actor_id === actor.id");
  assert.ok(actorDetailSrc.includes("conversation_id") &&
    (actorDetailSrc.includes('to="/admin/conversations/$conversationId"') ||
      /\/admin\/conversations\/\$\{.*conversation_id/.test(actorDetailSrc)),
    "each conversation row must link to the conversation view route");
});

test("actor-actions.tsx is either deleted or its Link still targets the actor-bound draft", () => {
  const actorActionsPath = path.join(here, "..", "components", "actor-actions.tsx");
  if (!existsSync(actorActionsPath)) {
    // Deleted — its link must have been inlined into the routes.
    assert.ok(actorsListSrc.includes("/admin/conversations/actor-${actor.id}") ||
      actorsListSrc.includes("`actor-${actor.id}`"),
      "inlined actor-bound draft link must be present in actors.tsx");
    return;
  }
  const actorActionsSrc = readFileSync(actorActionsPath, "utf8");
  assert.ok(actorActionsSrc.includes("`actor-${actor.id}`") ||
    actorActionsSrc.includes("'actor-' + actor.id"),
    "actor-actions Link must still target /admin/conversations/actor-<actor.id>");
});
