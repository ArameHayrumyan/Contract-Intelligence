import { Badge } from "@/components/ui/Badge";

/** Back-compat wrapper around the unified Badge (risk variant). */
export function RiskBadge({ score }: { score: number }) {
  return <Badge kind="risk" score={score} />;
}
