"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ActivityTimeline } from "@/components/ActivityTimeline";
import { getActivity } from "@/lib/api-client";
import type { ActivityEntry } from "@/lib/types";

const ACTION_GROUPS: { label: string; actions: string[] }[] = [
  { label: "Audit Actions", actions: ["audit_run", "crossref_run"] },
  {
    label: "Review Actions",
    actions: [
      "status_changed",
      "bulk_status_changed",
      "annotation_added",
      "annotation_updated",
      "annotation_deleted",
    ],
  },
  {
    label: "Export Actions",
    actions: ["document_exported", "portfolio_exported", "bulk_exported"],
  },
];

const RANGES: Record<string, number | null> = {
  "Last 7 days": 7,
  "Last 30 days": 30,
  "All time": null,
};

/** Compliance activity log — timeline with action + date-range filters. */
export default function ActivityPage() {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [selectedActions, setSelectedActions] = useState<Set<string>>(new Set());
  const [range, setRange] = useState("Last 30 days");

  const load = useCallback(async () => {
    const page = await getActivity(1, 100);
    setEntries(page.items);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function toggle(action: string) {
    setSelectedActions((prev) => {
      const next = new Set(prev);
      if (next.has(action)) next.delete(action);
      else next.add(action);
      return next;
    });
  }

  const filtered = useMemo(() => {
    const days = RANGES[range] ?? null;
    const cutoff = days === null ? 0 : Date.now() - days * 86400000;
    return entries.filter((e) => {
      if (selectedActions.size > 0 && !selectedActions.has(e.action)) return false;
      if (cutoff && new Date(e.created_at).getTime() < cutoff) return false;
      return true;
    });
  }, [entries, selectedActions, range]);

  return (
    <div>
      <h1>Activity</h1>

      <div className="panel">
        <div className="filter-bar">
          {ACTION_GROUPS.map((group) => (
            <div key={group.label} className="action-group">
              <strong className="muted">{group.label}</strong>
              {group.actions.map((action) => (
                <label key={action} className="check-row">
                  <input
                    type="checkbox"
                    checked={selectedActions.has(action)}
                    onChange={() => toggle(action)}
                  />
                  {action}
                </label>
              ))}
            </div>
          ))}
          <label className="filter-field">
            <span>Date range</span>
            <select value={range} onChange={(e) => setRange(e.target.value)}>
              {Object.keys(RANGES).map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <ActivityTimeline entries={filtered} />
    </div>
  );
}
