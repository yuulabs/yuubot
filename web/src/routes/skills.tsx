import { createFileRoute } from "@tanstack/react-router";

import { SkillsPage } from "@/features/skills";

export const Route = createFileRoute("/skills")({
  component: SkillsPage,
});
