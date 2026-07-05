import type { ReactNode } from "react";

export function ChatLayout({ rail, main, railOpen = true }: { rail: ReactNode; main: ReactNode; railOpen?: boolean }) {
  return (
    <div className="view view--conversations">
      <div className={railOpen ? "chat" : "chat chat--history-hidden"}>
        {railOpen && rail}
        {main}
      </div>
    </div>
  );
}

export function ChatMain({ children }: { children: ReactNode }) {
  return <div className="chat__main">{children}</div>;
}
