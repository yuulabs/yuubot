import { Card, CardContent } from "@/components/ui/card";

interface StatCardProps {
  label: string;
  value: number;
  sub: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  className?: string;
}

export function StatCard({ label, value, sub, icon: Icon, className }: StatCardProps) {
  return (
    <Card className={className}>
      <CardContent className="pt-6">
        <div className="text-sm font-medium text-muted-foreground">
          {Icon && <Icon size={16} className="mr-2 inline" />}
          {label}
        </div>
        <div className="mt-1 text-3xl font-bold">{value}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>
      </CardContent>
    </Card>
  );
}
