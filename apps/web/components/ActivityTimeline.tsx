"use client";

import Link from "next/link";

import type { ActivityEntry } from "@/lib/types";

/** Coarse category → dot colour class for an action. */
function dotClass(action: string): string {
  if (action.endsWith("_exported")) return "dot--grey";
  if (action.startsWith("annotation_")) return "dot--purple";
  if (action.includes("status")) return "dot--amber";
  return "dot--blue"; // audit_run / crossref_run
}

/** Plain-English label for an activity entry. */
function label(entry: ActivityEntry): string {
  const to = entry.to_value ?? {};
  const from = entry.from_value ?? {};
  const meta = entry.metadata ?? {};
  switch (entry.action) {
    case "audit_run":
      return "Contract audit completed";
    case "crossref_run":
      return "Cross-reference completed";
    case "status_changed":
      return `Status changed from ${from.status ?? "?"} to ${to.status ?? "?"}`;
    case "bulk_status_changed":
      return `${meta.count ?? "?"} contracts updated to ${to.status ?? "?"}`;
    case "annotation_added":
      return `Review note added to ${entry.target_type ?? "document"}`;
    case "annotation_updated":
      return "Review note updated";
    case "annotation_deleted":
      return "Review note deleted";
    case "document_exported":
      return "Document exported";
    case "portfolio_exported":
      return "Portfolio exported";
    case "bulk_exported":
      return `${meta.count ?? "?"} contracts exported`;
    default:
      return entry.action;
  }
}

function relativeTime(iso: string): string {
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days < 1) return "today";
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Vertical audit-trail timeline for a list of activity entries. */
export function ActivityTimeline({ entries }: { entries: ActivityEntry[] }) {
  if (entries.length === 0) {
    return <p className="muted">No activity recorded.</p>;
  }
  return (
    <div className="timeline">
      {entries.map((entry) => (
        <div key={entry.id} className="timeline-row">
          <span className={`timeline-dot ${dotClass(entry.action)}`} />
          <div className="timeline-body">
            <div className="timeline-label">{label(entry)}</div>
            {entry.document_id ? (
              <Link href={`/audit/${entry.document_id}`} className="muted">
                {entry.document_id}
              </Link>
            ) : null}
            <div className="muted timeline-meta">
              {entry.actor} · {relativeTime(entry.created_at)}
            </div>
            {entry.from_value || entry.to_value ? (
              <details>
                <summary className="muted">Details</summary>
                <pre className="code-block">
                  {JSON.stringify(
                    { from: entry.from_value, to: entry.to_value },
                    null,
                    2,
                  )}
                </pre>
              </details>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
