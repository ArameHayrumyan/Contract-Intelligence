"use client";

import Link from "next/link";

import type { ActivityEntry } from "@/lib/types";

function category(action: string): "audit" | "review" | "export" {
  if (action.endsWith("_exported")) return "export";
  if (action.startsWith("annotation_") || action.includes("status")) return "review";
  return "audit";
}

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

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

function relTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface ActivityTimelineProps {
  entries: ActivityEntry[];
  vendorById?: Record<string, string>;
}

/** Day-grouped audit-trail timeline with expandable before/after diffs. */
export function ActivityTimeline({ entries, vendorById = {} }: ActivityTimelineProps) {
  if (entries.length === 0) {
    return <p className="muted">No activity recorded.</p>;
  }
  let lastDay = "";
  return (
    <div className="timeline">
      {entries.map((entry) => {
        const day = dayLabel(entry.created_at);
        const showDay = day !== lastDay;
        lastDay = day;
        const cat = category(entry.action);
        const vendor = entry.document_id
          ? vendorById[entry.document_id] ?? "Contract"
          : null;
        return (
          <div className="timeline-event" key={entry.id}>
            <div className="timeline-date">{showDay ? day : ""}</div>
            <div className="timeline-track">
              <span className="timeline-line" />
              <span className={`timeline-dot timeline-dot--${cat}`} />
            </div>
            <div className="timeline-card" style={{ gridColumn: "2" }}>
              <div className="timeline-card__top">
                <span style={{ fontWeight: 500 }}>{label(entry)}</span>
                <span className="text-muted">{relTime(entry.created_at)}</span>
              </div>
              {entry.document_id ? (
                <div className="muted" style={{ fontSize: "var(--text-sm)" }}>
                  <Link href={`/audit/${entry.document_id}`}>{vendor}</Link>{" "}
                  <span className="mono text-muted">
                    · {entry.document_id.slice(0, 8)}
                  </span>
                </div>
              ) : null}
              <div className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
                {entry.actor}
              </div>
              {entry.from_value || entry.to_value ? (
                <details>
                  <summary className="muted" style={{ fontSize: "var(--text-xs)" }}>
                    Details
                  </summary>
                  <div className="diff-grid" style={{ marginTop: "var(--space-2)" }}>
                    <div>
                      <div className="text-muted">Before</div>
                      <pre className="code-block">
                        {JSON.stringify(entry.from_value ?? {}, null, 2)}
                      </pre>
                    </div>
                    <div>
                      <div className="text-muted">After</div>
                      <pre className="code-block">
                        {JSON.stringify(entry.to_value ?? {}, null, 2)}
                      </pre>
                    </div>
                  </div>
                </details>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
