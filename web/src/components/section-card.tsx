import { Card, CardHeader, CardTitle, CardContent, CardAction } from "@/components/ui/card";
import { Link } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";

interface SectionCardProps {
  title: string;
  children: React.ReactNode;
  actionLabel?: string;
  actionTo?: string;
  className?: string;
}

export function SectionCard({ title, children, actionLabel, actionTo, className }: SectionCardProps) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {actionLabel && actionTo && (
          <CardAction>
            <Button variant="ghost" size="xs" asChild>
              <Link to={actionTo}>{actionLabel}</Link>
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}
