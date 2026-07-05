import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";

export function JsonEditor<T>({
  initial,
  onSave,
  saveLabel = "Save",
}: {
  initial: T;
  onSave: (value: T) => Promise<unknown>;
  saveLabel?: string;
}) {
  const [text, setText] = useState(() => JSON.stringify(initial, null, 2));
  const [error, setError] = useState("");
  const mutation = useMutation({ mutationFn: onSave });
  return (
    <div className="stack">
      <textarea className="textarea font-mono" rows={14} value={text} onChange={(event) => setText(event.target.value)} />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {mutation.error && <p className="text-sm text-destructive">{String(mutation.error)}</p>}
      <Button
        type="button"
        disabled={mutation.isPending}
        onClick={() => {
          setError("");
          try {
            void mutation.mutateAsync(JSON.parse(text) as T);
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      >
        {saveLabel}
      </Button>
    </div>
  );
}
