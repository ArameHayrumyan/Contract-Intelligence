"use client";

import { Fragment, useState } from "react";

import { AnnotationPanel } from "@/components/AnnotationPanel";
import { Badge } from "@/components/ui/Badge";
import type { ClauseDeviation } from "@/lib/types";

function Expandable({ text }: { text: string | null }) {
  const [open, setOpen] = useState(false);
  if (!text) return <span className="cell-empty">—</span>;
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

interface DeviationTableProps {
  deviations: ClauseDeviation[];
  /** When set, each row gets an expandable annotation footer. */
  documentId?: string;
}

/** Sortable, colour-coded deviation table with inline annotation expansion. */
export function DeviationTable({ deviations, documentId }: DeviationTableProps) {
  const [desc, setDesc] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);
  const rows = [...deviations].sort((a, b) =>
    desc ? b.severity - a.severity : a.severity - b.severity,
  );

  if (rows.length === 0) {
    return <p className="muted">No deviations detected.</p>;
  }

  return (
    <div className="table-wrap">
    <table className="data-table">
      <thead>
        <tr>
          <th>Clause type</th>
          <th>Deviation</th>
          <th className="is-sortable" onClick={() => setDesc((v) => !v)}>
            Severity {desc ? "↓" : "↑"}
          </th>
          <th>Subject text</th>
          <th>Standard text</th>
          <th>Explanation</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d, i) => {
          const open = openId !== null && openId === d.deviation_id;
          return (
            <Fragment key={`${d.clause_type}-${i}`}>
              <tr>
                <td>{d.clause_type}</td>
                <td>
                  <Badge kind="deviation" deviation={d.deviation_type} />
                </td>
                <td>{d.severity}</td>
                <td>
                  <Expandable text={d.subject_text} />
                  {d.subject_page != null ? (
                    <div className="text-muted">subject p.{d.subject_page}</div>
                  ) : null}
                </td>
                <td>
                  <Expandable text={d.standard_text} />
                  {d.standard_page != null ? (
                    <div className="text-muted">standard p.{d.standard_page}</div>
                  ) : null}
                </td>
                <td>
                  {d.explanation}
                  {documentId && d.deviation_id ? (
                    <div>
                      <button
                        className="link-btn"
                        onClick={() => setOpenId(open ? null : d.deviation_id)}
                      >
                        {open ? "Hide annotations" : "Annotations"}
                      </button>
                    </div>
                  ) : null}
                </td>
              </tr>
              {open && documentId && d.deviation_id ? (
                <tr>
                  <td colSpan={6}>
                    <AnnotationPanel
                      documentId={documentId}
                      targetType="deviation"
                      targetReference={d.deviation_id}
                    />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}
