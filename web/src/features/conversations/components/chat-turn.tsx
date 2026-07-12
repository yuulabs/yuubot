import { memo } from "react";
import type { DisplayItem } from "../lib/conversation-transcript";
import { MessageBlockView } from "./message-block-view";
import type { AskUserAnswerInput } from "@/shared/lib/api";

export const ChatTurn = memo(function ChatTurn({ actorId, item, onAnswerQuestion }: {
  actorId: string;
  item: DisplayItem;
  onAnswerQuestion: (toolCallId: string, answers: AskUserAnswerInput[], skipped?: boolean) => boolean;
}) {
  const isUser = item.role === "user";
  const roleClass = isUser ? "msg msg--user" : "msg msg--assistant";
  const avatar = isUser ? "U" : "A";
  const label = isUser ? "you" : "assistant";

  return (
    <article className={roleClass}>
      <div className="msg__avatar">{avatar}</div>
      <div className="msg__body">
        <div className="msg__meta">
          <span>{label}</span>
          {item.createdAt && <span>{item.createdAt}</span>}
        </div>
        <div className="msg__bubble msg__bubble--blocks">
          {item.blocks.map((block) => (
            <MessageBlockView
              key={block.key}
              actorId={actorId}
              block={block}
              isStreaming={Boolean(item.streaming && (block.type === "thinking" || block.type === "text"))}
              onAnswerQuestion={onAnswerQuestion}
            />
          ))}
          {item.streaming && item.blocks.every((block) => block.type !== "text" && block.type !== "thinking") && (
            <span className="stream-cursor" aria-hidden="true" />
          )}
        </div>
      </div>
    </article>
  );
}, (previous, next) => (
  previous.actorId === next.actorId
  && previous.item.key === next.item.key
  && !previous.item.streaming
  && !next.item.streaming
  && sameBlockTail(previous.item, next.item)
));

function sameBlockTail(left: DisplayItem, right: DisplayItem): boolean {
  if (left.blocks.length !== right.blocks.length) return false;
  const leftTail = left.blocks[left.blocks.length - 1];
  const rightTail = right.blocks[right.blocks.length - 1];
  return leftTail === undefined || (
    leftTail.key === rightTail?.key
    && leftTail.content === rightTail.content
    && leftTail.toolArgs === rightTail.toolArgs
    && leftTail.toolResult === rightTail.toolResult
    && leftTail.toolStatus === rightTail.toolStatus
  );
}
