import { useEffect, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";

export function ChatLayout({
  rail,
  main,
  railOpen = true,
  onRailOpenChange,
}: {
  rail: ReactNode;
  main: ReactNode;
  railOpen?: boolean;
  onRailOpenChange?: (open: boolean) => void;
}) {
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 860px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return (
    <div className="view view--conversations">
      <div className={mobile || !railOpen ? "chat chat--history-hidden" : "chat"}>
        {!mobile && railOpen && rail}
        {main}
      </div>
      {mobile && (
        <DialogPrimitive.Root open={railOpen} onOpenChange={onRailOpenChange}>
          <DialogPrimitive.Portal>
            <DialogPrimitive.Overlay className="chat-history-drawer__overlay" />
            <DialogPrimitive.Content className="chat-history-drawer" aria-describedby={undefined}>
              <DialogPrimitive.Title className="sr-only">Conversation history</DialogPrimitive.Title>
              {rail}
              <DialogPrimitive.Close className="chat-history-drawer__close" aria-label="Close conversation history">
                <X size={18} />
              </DialogPrimitive.Close>
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
      )}
    </div>
  );
}

export function ChatMain({ children }: { children: ReactNode }) {
  return <div className="chat__main">{children}</div>;
}
