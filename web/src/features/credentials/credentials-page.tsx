import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Trash2 } from "lucide-react";

import { deleteCredential, listCredentials } from "@/shared/lib/api";
import type { CredentialRecord } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Page,
  ResourceCard,
  ResourceCardGrid,
  ResourceMeta,
  Status,
} from "@/shared/components";

const queryKey = ["credentials"] as const;

export function CredentialsPage() {
  const query = useQuery({ queryKey, queryFn: listCredentials });
  const client = useQueryClient();
  const remove = useMutation({
    mutationFn: deleteCredential,
    onSuccess: () => client.invalidateQueries({ queryKey }),
  });
  const error = query.error ?? remove.error;

  if (query.isLoading) return <LoadingState />;

  return (
    <Page title="Credentials" sub="Daemon-managed credential metadata. Secret payloads are never exposed.">
      {error && <ErrorState error={error} />}
      {!query.data?.length ? (
        <EmptyState>No credentials stored.</EmptyState>
      ) : (
        <ResourceCardGrid>
          {query.data.map((credential) => (
            <CredentialCard key={credential.id} credential={credential} onDelete={remove.mutate} />
          ))}
        </ResourceCardGrid>
      )}
    </Page>
  );
}

function CredentialCard({
  credential,
  onDelete,
}: {
  credential: CredentialRecord;
  onDelete: (credentialId: string) => void;
}) {
  return (
    <ResourceCard
      variant="provider"
      label={credential.kind}
      title={credential.label || credential.id}
      subtitle={credential.provider}
      status={<Status enabled label={credential.redacted_summary || "configured"} />}
      actions={
        <Button variant="outline" size="sm" onClick={() => onDelete(credential.id)}>
          <Trash2 size={14} />
        </Button>
      }
    >
      <ResourceMeta
        items={[
          { label: "ID", value: credential.id },
          { label: "Provider", value: credential.provider },
          { label: "Scope", value: credential.owner_scope },
          { label: "Expires", value: credential.expires_at ?? "not reported" },
          { label: "Scopes", value: credential.scopes.length ? credential.scopes.join(" ") : "none" },
        ]}
      />
      <pre className="resource-preview">
        <KeyRound size={14} /> {credential.secret_ref}
      </pre>
    </ResourceCard>
  );
}
