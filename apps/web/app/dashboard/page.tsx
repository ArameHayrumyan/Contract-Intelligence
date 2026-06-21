"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  cleanVendor,
  ContractPanel,
  endDateClass,
  formatDate,
} from "@/components/ContractPanel";
import { UploadModal } from "@/components/UploadModal";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import {
  bulkExport,
  bulkUpdateStatus,
  downloadPdf,
  getDashboardSummary,
  listContracts,
} from "@/lib/api-client";
import type { ContractRow, DashboardSummary, WorkflowStatus } from "@/lib/types";

const STATUSES: WorkflowStatus[] = ["audited", "reviewed", "approved", "flagged"];
const PAGE_SIZE = 20;

type RiskLevel = "all" | "low" | "medium" | "high";
type SortKey = "created_at" | "risk_score" | "vendor_name" | "contract_end_date";

function avgClass(score: number) {
  if (score >= 8) return "stat-card__value--high";
  if (score >= 4) return "stat-card__value--medium";
  return "stat-card__value--low";
}

export default function DashboardPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [all, setAll] = useState<ContractRow[] | null>(null);
  const [selected, setSelected] = useState<ContractRow | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [exportingPortfolio, setExportingPortfolio] = useState(false);

  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [risk, setRisk] = useState<RiskLevel>("all");
  const [auto, setAuto] = useState<"all" | "yes" | "no">("all");
  const [status, setStatus] = useState<"all" | WorkflowStatus>("all");
  const [sortBy, setSortBy] = useState<SortKey>("created_at");
  const [page, setPage] = useState(1);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkStatus, setBulkStatus] = useState<WorkflowStatus>("reviewed");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [s, page_] = await Promise.all([
      getDashboardSummary(),
      listContracts({ page: 1, page_size: 1000, sort_by: "created_at", sort_order: "desc" }),
    ]);
    setSummary(s);
    setAll(page_.items);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim().toLowerCase()), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Close the row action menu on any outside click or Escape.
  useEffect(() => {
    if (!menuFor) return undefined;
    function onClick() {
      setMenuFor(null);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuFor(null);
    }
    window.addEventListener("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuFor]);

  // Selection does not span pages — clear it when the page changes.
  useEffect(() => {
    setSelectedIds((prev) => {
      if (prev.size > 0) toast("Selection cleared (page changed).", "info");
      return new Set();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const activeFilters =
    (risk !== "all" ? 1 : 0) +
    (auto !== "all" ? 1 : 0) +
    (status !== "all" ? 1 : 0) +
    (debounced ? 1 : 0);

  const filtered = useMemo(() => {
    if (!all) return [];
    let rows = all;
    if (debounced)
      rows = rows.filter((r) => (r.vendor_name ?? "").toLowerCase().includes(debounced));
    if (risk !== "all")
      rows = rows.filter((r) => {
        const lvl = r.risk_score >= 8 ? "high" : r.risk_score >= 4 ? "medium" : "low";
        return lvl === risk;
      });
    if (auto !== "all") rows = rows.filter((r) => r.auto_renewal === (auto === "yes"));
    if (status !== "all") rows = rows.filter((r) => r.status === status);
    const sorted = [...rows].sort((a, b) => {
      if (sortBy === "risk_score") return b.risk_score - a.risk_score;
      if (sortBy === "vendor_name")
        return (a.vendor_name ?? "").localeCompare(b.vendor_name ?? "");
      if (sortBy === "contract_end_date")
        return (a.contract_end_date ?? "9999").localeCompare(b.contract_end_date ?? "9999");
      return b.created_at.localeCompare(a.created_at);
    });
    return sorted;
  }, [all, debounced, risk, auto, status, sortBy]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function clearFilters() {
    setSearch("");
    setRisk("all");
    setAuto("all");
    setStatus("all");
    setPage(1);
  }

  function toggleRow(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelectedIds((prev) =>
      prev.size === pageRows.length
        ? new Set()
        : new Set(pageRows.map((r) => r.document_id)),
    );
  }

  async function applyBulk() {
    setBulkBusy(true);
    try {
      const res = await bulkUpdateStatus([...selectedIds], bulkStatus);
      toast(`Updated ${res.updated} contract(s).`, "success");
      setSelectedIds(new Set());
      await load();
    } catch {
      toast("Bulk update failed.", "error");
    } finally {
      setBulkBusy(false);
    }
  }
  async function exportSelected() {
    setBulkBusy(true);
    try {
      await bulkExport([...selectedIds]);
    } finally {
      setBulkBusy(false);
    }
  }
  async function exportPortfolio() {
    setExportingPortfolio(true);
    try {
      await downloadPdf("/api/portfolio/export/pdf", "portfolio_report.pdf");
    } finally {
      setExportingPortfolio(false);
    }
  }
  async function exportRow(id: string, vendor: string) {
    await downloadPdf(
      `/api/documents/${encodeURIComponent(id)}/export/pdf`,
      `audit_${vendor}.pdf`,
    );
  }

  const crossrefId = selectedIds.size === 1 ? [...selectedIds][0] : null;

  return (
    <div className="container--wide">
      <h1 className="page-header__title" style={{ marginBottom: "var(--space-5)" }}>
        Portfolio Dashboard
      </h1>

      {/* Stat cards */}
      {summary ? (
        <div className="stat-row">
          <StatCard label="Total Contracts" value={summary.total_contracts} />
          <StatCard
            label="Avg Risk Score"
            value={summary.avg_risk_score}
            valueClass={avgClass(summary.avg_risk_score)}
          />
          <StatCard
            label="High Risk"
            value={summary.risk_distribution.high}
            valueClass={summary.risk_distribution.high > 0 ? "stat-card__value--high" : ""}
            alert={summary.risk_distribution.high > 0 ? "high" : undefined}
          />
          <StatCard label="Auto-Renewal Active" value={summary.contracts_with_autorenewal} />
          <StatCard
            label="Expiring Soon (60d)"
            value={summary.contracts_expiring_soon}
            valueClass={
              summary.contracts_expiring_soon > 0 ? "stat-card__value--medium" : ""
            }
            alert={summary.contracts_expiring_soon > 0 ? "medium" : undefined}
          />
        </div>
      ) : (
        <div className="stat-row">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} height={108} borderRadius="var(--radius-lg)" />
          ))}
        </div>
      )}

      {/* Quick action bar */}
      <div className="action-bar">
        <Button onClick={() => setUploadOpen(true)}>Upload Contract</Button>
        <Button
          variant="secondary"
          disabled={!crossrefId}
          title={crossrefId ? "" : "Select a contract from the table first"}
          onClick={() => crossrefId && router.push(`/crossref/${crossrefId}`)}
        >
          Run Cross-Reference
        </Button>
        <span className="action-bar__spacer" />
        <Button variant="ghost" onClick={exportPortfolio} loading={exportingPortfolio}>
          ⤓ Export Portfolio Report
        </Button>
      </div>

      {/* Filter bar */}
      <div className="filter-bar">
        <input
          className="input"
          placeholder="Search vendor…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <Select value={risk} onChange={(v) => { setRisk(v as RiskLevel); setPage(1); }}
          options={[["all", "All risk"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]]} />
        <Select value={auto} onChange={(v) => { setAuto(v as typeof auto); setPage(1); }}
          options={[["all", "Auto-renewal: All"], ["yes", "Auto-renewal: Yes"], ["no", "Auto-renewal: No"]]} />
        <Select value={status} onChange={(v) => { setStatus(v as typeof status); setPage(1); }}
          options={[["all", "All statuses"], ...STATUSES.map((s) => [s, s] as [string, string])]} />
        <Select value={sortBy} onChange={(v) => setSortBy(v as SortKey)}
          options={[["created_at", "Sort: Upload date"], ["risk_score", "Sort: Risk"], ["vendor_name", "Sort: Vendor"]]} />
        <span className="filter-bar__right">
          {activeFilters > 0 ? (
            <>
              <span>Filters · {activeFilters}</span>
              <button className="link-btn" onClick={clearFilters}>Clear all</button>
            </>
          ) : null}
          <span>{filtered.length} contracts</span>
        </span>
      </div>

      {/* Table / states */}
      {all === null ? (
        <Skeleton height={240} borderRadius="var(--radius-lg)" />
      ) : filtered.length === 0 ? (
        activeFilters > 0 ? (
          <EmptyState
            title="No contracts match your filters"
            description="Try adjusting the filters above."
            action={<Button variant="secondary" onClick={clearFilters}>Clear filters</Button>}
          />
        ) : (
          <EmptyState
            title="No contracts yet"
            description="Upload your first contract to begin auditing."
            action={<Button onClick={() => setUploadOpen(true)}>Upload Contract</Button>}
          />
        )
      ) : (
        <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 40 }}>
                <input
                  type="checkbox"
                  className="checkbox"
                  checked={pageRows.length > 0 && selectedIds.size === pageRows.length}
                  onChange={toggleAll}
                  aria-label="Select all"
                />
              </th>
              <SortableTh label="Vendor" col="vendor_name" sortBy={sortBy} onSort={setSortBy} />
              <th>Type</th>
              <SortableTh label="Risk" col="risk_score" sortBy={sortBy} onSort={setSortBy} />
              <th>Auto-Renewal</th>
              <th>Notice</th>
              <SortableTh label="End Date" col="contract_end_date" sortBy={sortBy} onSort={setSortBy} />
              <th>Status</th>
              <SortableTh label="Uploaded" col="created_at" sortBy={sortBy} onSort={setSortBy} />
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((r) => (
              <tr
                key={r.document_id}
                className="is-clickable"
                onClick={() => setSelected(r)}
              >
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    className="checkbox"
                    checked={selectedIds.has(r.document_id)}
                    onChange={() => toggleRow(r.document_id)}
                    aria-label={`Select ${r.vendor_name ?? "contract"}`}
                  />
                </td>
                <td>{cleanVendor(r.vendor_name) ?? <span className="cell-empty">—</span>}</td>
                <td>{cleanVendor(r.contract_type) ?? <span className="cell-empty">—</span>}</td>
                <td><Badge kind="risk" score={r.risk_score} /></td>
                <td>{r.auto_renewal ? "Yes" : "No"}</td>
                <td>{r.notice_period_days ?? "—"}</td>
                <td className={endDateClass(r.contract_end_date)}>
                  {formatDate(r.contract_end_date)}
                </td>
                <td><Badge kind="status" status={r.status} /></td>
                <td>{formatDate(r.created_at)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="row-actions">
                    <button className="link-btn" onClick={() => router.push(`/audit/${r.document_id}`)}>
                      View
                    </button>
                    <button className="link-btn" onClick={() => exportRow(r.document_id, cleanVendor(r.vendor_name) ?? "contract")}>
                      Export
                    </button>
                    <div className="dropdown">
                      <button
                        className="icon-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          setMenuFor(menuFor === r.document_id ? null : r.document_id);
                        }}
                        aria-label="More"
                      >
                        ⋮
                      </button>
                      {menuFor === r.document_id ? (
                        <div className="dropdown__menu" onClick={(e) => e.stopPropagation()}>
                          <button className="dropdown__item" onClick={() => router.push(`/crossref/${r.document_id}`)}>
                            Run Cross-Reference
                          </button>
                          <button className="dropdown__item" onClick={() => router.push(`/audit/${r.document_id}`)}>
                            Change Status
                          </button>
                          <button className="dropdown__item" onClick={() => router.push(`/audit/${r.document_id}?tab=activity`)}>
                            View Activity
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {filtered.length > PAGE_SIZE ? (
        <div className="pager">
          <button className="link-btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            ← Previous
          </button>
          <span>Page {page} of {totalPages}</span>
          <button className="link-btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
            Next →
          </button>
        </div>
      ) : null}

      {/* Bulk action bar */}
      {selectedIds.size > 0 ? (
        <div className="bulk-bar">
          <span>{selectedIds.size} selected</span>
          <div className="bulk-bar__center">
            <select
              className="select"
              value={bulkStatus}
              onChange={(e) => setBulkStatus(e.target.value as WorkflowStatus)}
            >
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <Button size="sm" onClick={applyBulk} loading={bulkBusy}>Apply</Button>
            <Button size="sm" variant="secondary" onClick={exportSelected} loading={bulkBusy}>
              Export Selected (ZIP)
            </Button>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set())}>
            Clear selection
          </Button>
        </div>
      ) : null}

      <UploadModal isOpen={uploadOpen} onClose={() => setUploadOpen(false)} onUploaded={load} />
      <ContractPanel contract={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function StatCard({
  label,
  value,
  valueClass = "",
  alert,
}: {
  label: string;
  value: number;
  valueClass?: string;
  alert?: "high" | "medium";
}) {
  return (
    <div className={`stat-card${alert ? ` stat-card--alert-${alert}` : ""}`}>
      <div className="stat-card__top">
        <DocIcon />
      </div>
      <div className={`stat-card__value ${valueClass}`}>{value}</div>
      <div className="stat-card__label">{label}</div>
    </div>
  );
}

function SortableTh({
  label,
  col,
  sortBy,
  onSort,
}: {
  label: string;
  col: SortKey;
  sortBy: SortKey;
  onSort: (k: SortKey) => void;
}) {
  const active = sortBy === col;
  return (
    <th className="is-sortable" onClick={() => onSort(col)}>
      {label}{" "}
      <span style={{ color: active ? "var(--color-text-primary)" : "var(--color-text-muted)" }}>
        {active ? "↓" : "↕"}
      </span>
    </th>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[][];
}) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </select>
  );
}

function DocIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M14 3H6v18h12V8z M14 3v5h4" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
