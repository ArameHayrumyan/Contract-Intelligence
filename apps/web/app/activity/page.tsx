"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ActivityTimeline } from "@/components/ActivityTimeline";
import { cleanVendor } from "@/components/ContractPanel";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { getActivity, listContracts } from "@/lib/api-client";
import type { ActivityEntry } from "@/lib/types";

type Category = "all" | "audit" | "review" | "export";

const CATEGORY_ACTIONS: Record<Exclude<Category, "all">, string[]> = {
  audit: ["audit_run", "crossref_run"],
  review: [
    "status_changed",
    "bulk_status_changed",
    "annotation_added",
    "annotation_updated",
    "annotation_deleted",
  ],
  export: ["document_exported", "portfolio_exported", "bulk_exported"],
};

const RANGES: Record<string, number | null> = {
  "Last 7 days": 7,
  "Last 30 days": 30,
  "All time": null,
};

export default function ActivityPage() {
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [nextPage, setNextPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [category, setCategory] = useState<Category>("all");
  const [range, setRange] = useState("Last 30 days");
  const [search, setSearch] = useState("");
  const [vendorById, setVendorById] = useState<Record<string, string>>({});

  // Accumulate pages (the API caps page_size at 100, so "load more" pages on).
  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const page = await getActivity(nextPage, 50);
      setTotal(page.total);
      setEntries((prev) => [...(prev ?? []), ...page.items]);
      setNextPage((p) => p + 1);
    } finally {
      setLoadingMore(false);
    }
  }, [nextPage]);

  useEffect(() => {
    void loadMore();
    // Initial load only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    listContracts({ page: 1, page_size: 1000 })
      .then((p) => {
        const map: Record<string, string> = {};
        p.items.forEach((c) => (map[c.document_id] = cleanVendor(c.vendor_name) ?? "Contract"));
        setVendorById(map);
      })
      .catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    if (!entries) return [];
    const days = RANGES[range] ?? null;
    const cutoff = days === null ? 0 : Date.now() - days * 86400000;
    const term = search.trim().toLowerCase();
    const allowed = category === "all" ? null : new Set(CATEGORY_ACTIONS[category]);
    return entries.filter((e) => {
      if (allowed && !allowed.has(e.action)) return false;
      if (cutoff && new Date(e.created_at).getTime() < cutoff) return false;
      if (term) {
        const vendor = e.document_id ? vendorById[e.document_id] ?? "" : "";
        const hay = `${e.actor} ${vendor} ${e.document_id ?? ""}`.toLowerCase();
        if (!hay.includes(term)) return false;
      }
      return true;
    });
  }, [entries, range, search, category, vendorById]);

  return (
    <div className="container">
      <PageHeader
        title="Audit Trail"
        subtitle="Immutable log of all actions in your workspace."
      />

      <div className="filter-bar">
        <div className="pill-group">
          {(["all", "audit", "review", "export"] as Category[]).map((c) => (
            <button
              key={c}
              className={`pill-toggle${category === c ? " is-active" : ""}`}
              onClick={() => setCategory(c)}
            >
              {c === "all"
                ? "All"
                : c === "audit"
                  ? "Audit Actions"
                  : c === "review"
                    ? "Review Actions"
                    : "Export Actions"}
            </button>
          ))}
        </div>
        <input
          className="input"
          placeholder="Search actor or document…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className="select" value={range} onChange={(e) => setRange(e.target.value)}>
          {Object.keys(RANGES).map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      </div>

      {entries === null ? (
        <Skeleton height={200} borderRadius="var(--radius-lg)" />
      ) : (
        <>
          <ActivityTimeline entries={filtered} vendorById={vendorById} />
          {entries.length < total ? (
            <div style={{ textAlign: "center", marginTop: "var(--space-4)" }}>
              <Button variant="secondary" onClick={loadMore} loading={loadingMore}>
                Load more
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
