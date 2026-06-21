import type { DeviationType, WorkflowStatus } from "@/lib/types";

type RiskLevel = "high" | "medium" | "low";

function riskLevel(score: number): RiskLevel {
  if (score >= 8) return "high";
  if (score >= 4) return "medium";
  return "low";
}

const RISK_LABEL: Record<RiskLevel, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

const DEVIATION_LABEL: Record<DeviationType, string> = {
  missing: "Missing",
  contradictory: "Contradictory",
  weakened: "Weakened",
  strengthened: "Strengthened",
  unaddressed: "Unaddressed",
};

interface RiskBadgeProps {
  kind: "risk";
  score: number;
  size?: "xs" | "md";
}
interface StatusBadgeProps {
  kind: "status";
  status: WorkflowStatus | string;
  size?: "xs" | "md";
}
interface DeviationBadgeProps {
  kind: "deviation";
  deviation: DeviationType;
  size?: "xs" | "md";
}

type BadgeProps = RiskBadgeProps | StatusBadgeProps | DeviationBadgeProps;

/** The single badge primitive — risk / status / deviation variants. */
export function Badge(props: BadgeProps) {
  const sizeClass = props.size === "xs" ? "badge--xs" : "";
  if (props.kind === "risk") {
    const level = riskLevel(props.score);
    return (
      <span
        className={`badge badge--risk-${level} ${sizeClass}`}
        title={`Risk score ${props.score}/10`}
      >
        {RISK_LABEL[level]} · {props.score}/10
      </span>
    );
  }
  if (props.kind === "status") {
    return (
      <span className={`badge badge--status-${props.status} ${sizeClass}`}>
        {props.status}
      </span>
    );
  }
  return (
    <span className={`badge badge--dev-${props.deviation} ${sizeClass}`}>
      {DEVIATION_LABEL[props.deviation]}
    </span>
  );
}
