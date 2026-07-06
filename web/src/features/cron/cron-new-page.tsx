import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { createCronJob } from "@/shared/lib/api";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, Page } from "@/shared/components";
import { useBootstrap } from "@/shared/hooks";
import { CronForm } from "./cron-form";
import { buildCronAction, buildCronOwner, buildCronSchedule } from "./cron-shared";

export function CronNewPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, error, isLoading } = useBootstrap();
  const create = useMutation({
    mutationFn: (draft: Parameters<typeof buildCronSchedule>[0]) =>
      createCronJob({
        name: draft.name,
        owner: buildCronOwner(draft.actorId, draft.conversationId),
        schedule: buildCronSchedule(draft),
        action: buildCronAction(draft),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["cron-jobs"] });
      await navigate({ to: "/cron" });
    },
  });

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;

  return (
    <Page
      title="New Cron Job"
      sub="Register a durable schedule for shell tasks, actor messages, conversation callbacks, or reminders."
      actions={
        <Button variant="outline" asChild>
          <Link to="/cron">Back to list</Link>
        </Button>
      }
    >
      {data && (
        <CronForm
          bootstrap={data}
          saveLabel={create.isPending ? "Creating..." : "Create Cron Job"}
          onSave={async (draft) => {
            await create.mutateAsync(draft);
          }}
        />
      )}
    </Page>
  );
}
