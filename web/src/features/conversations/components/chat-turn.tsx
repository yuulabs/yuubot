import type { DisplayItem } from "../lib/conversation-transcript";
import { MessageBlockView } from "./message-block-view";

export function ChatTurn({ item }: { item: DisplayItem }) {
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
              block={block}
              isStreaming={Boolean(item.streaming && (block.type === "thinking" || block.type === "text"))}
            />
          ))}
          {item.streaming && item.blocks.every((block) => block.type !== "text" && block.type !== "thinking") && (
            <span className="stream-cursor" aria-hidden="true" />
          )}
        </div>
      </div>
    </article>
  );
}
