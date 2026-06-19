"use client";

import { useState } from "react";

import type { ClauseDeviation, DeviationType } from "@/lib/types";

/** Human labels for each deviation type. */
const LABEL: Record<DeviationType, string> = {
  missing: "Missing",
  weakened: "Weakened",
  strengthened: "Strengthened",
  contradictory: "Contradictory",
  unaddressed: "Unaddressed",
};

interface DeviationTableProps {
  deviations: ClauseDeviation[];
}

function Expandable({ text }: { text: string | null }) {
  const [open, setOpen] = useState(false);
  if (!text) return <span className="muted">—</span>;
  const truncated = text.length > 120 && !open;
  return (
    <span>
      {truncated ? `${text.slice(0, 120)}… ` : `${text} `}
      {text.length > 120 ? (
        <button className="link-btn" onClick={() => setOpen((v) => !v)}>
          {open ? "less" : "more"}
        </button>
      ) : null}
    </span>
  );
}

/** Sortable, color-coded table of clause deviations (severity desc default). */
export function DeviationTable({ deviations }: DeviationTableProps) {
  const [desc, setDesc] = useState(true);
  const rows = [...deviations].sort((a, b) =>
    desc ? b.severity - a.severity : a.severity - b.severity,
  );

  if (rows.length === 0) {
    return <p className="muted">No deviations detected.</p>;
  }

  return (
    <table className="dev-table">
      <thead>
        <tr>
          <th>Clause type</th>
          <th>Deviation</th>
          <th
            className="sortable"
            onClick={() => setDesc((v) => !v)}
            title="Sort by severity"
          >
            Severity {desc ? "▼" : "▲"}
          </th>
          <th>Subject text</th>
          <th>Standard text</th>
          <th>Explanation</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d, i) => (
          <tr key={`${d.clause_type}-${i}`} className={`dev dev--${d.deviation_type}`}>
            <td>{d.clause_type}</td>
            <td>
              <span className={`pill pill--${d.deviation_type}`}>
                {LABEL[d.deviation_type]}
              </span>
            </td>
            <td className="sev">{d.severity}</td>
            <td>
              <Expandable text={d.subject_text} />
              {d.subject_page != null ? (
                <div className="prov">subject p.{d.subject_page}</div>
              ) : null}
            </td>
            <td>
              <Expandable text={d.standard_text} />
              {d.standard_page != null ? (
                <div className="prov">standard p.{d.standard_page}</div>
              ) : null}
            </td>
            <td>{d.explanation}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
