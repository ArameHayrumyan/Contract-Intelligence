/**
 * Colour-coded risk badge: green 1-3, amber 4-7, red 8-10 (Section 3.9).
 */

interface RiskBadgeProps {
  /** Risk score on the inclusive 1-10 scale. */
  score: number;
}

type Band = "low" | "medium" | "high";

function band(score: number): Band {
  if (score <= 3) return "low";
  if (score <= 7) return "medium";
  return "high";
}

const LABEL: Record<Band, string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
};

/** Renders a pill reflecting the contract's risk band. */
export function RiskBadge({ score }: RiskBadgeProps) {
  const b = band(score);
  return (
    <span className={`badge badge--${b}`} title={`Risk score ${score}/10`}>
      {LABEL[b]} · {score}/10
    </span>
  );
}
