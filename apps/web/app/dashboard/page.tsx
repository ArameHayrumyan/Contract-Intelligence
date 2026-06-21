"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ContractPanel, formatDate } from "@/components/ContractPanel";
import { RiskBadge } from "@/components/RiskBadge";
import {
  bulkExport,
  bulkUpdateStatus,
  downloadPdf,
  getDashboardSummary,
  listContracts,
} from "@/lib/api-client";
import type {
  ContractFilters,
  ContractRow,
  DashboardSummary,
  WorkflowStatus,
} from "@/lib/types";

const STATUS_OPTIONS: WorkflowStatus[] = [
  "audited",
  "reviewed",
  "approved",
  "flagged",
];

type RiskLevel = "all" | "low" | "medium" | "high";

const RISK_RANGES: Record<RiskLevel, Partial<ContractFilters>> = {
  all: {},
  low: { risk_score_max: 3 },
  medium: { risk_score_min: 4, risk_score_max: 7 },
  high: { risk_score_min: 8 },
};

function avgBand(score: number): "low" | "medium" | "high" {
  if (score <= 3) return "low";
  if (score <= 7) return "medium";
  return "high";
}

/** Portfolio dashboard: stats, filterable contract table, and a detail drawer. */
export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [rows, setRows] = useState<ContractRow[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<ContractRow | null>(null);
  const [exportingPortfolio, setExportingPortfolio] = useState(false);

  const [risk, setRisk] = useState<RiskLevel>("all");
  const [autoRenewal, setAutoRenewal] = useState<"all" | "yes" | "no">("all");
  const [statusFilter, setStatusFilter] = useState("");
  const [sortBy, setSortBy] = useState<ContractFilters["sort_by"]>("created_at");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkStatus, setBulkStatus] = useState<WorkflowStatus>("reviewed");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const filters = useMemo<ContractFilters>(() => {
    const f: ContractFilters = {
      ...RISK_RANGES[risk],
      sort_by: sortBy,
      sort_order: "desc",
      page,
      page_size: pageSize,
    };
    if (autoRenewal !== "all") f.auto_renewal = autoRenewal === "yes";
    if (statusFilter) f.status = statusFilter as ContractFilters["status"];
    return f;
  }, [risk, autoRenewal, statusFilter, sortBy, page, pageSize]);

  const load = useCallback(async () => {
    const [s, page_] = await Promise.all([
      getDashboardSummary(),
      listContracts(filters),
    ]);
    setSummary(s);
    setRows(page_.items);
    setTotal(page_.total);
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  // Selection does not span pages — clear it (with a toast) when the page moves.
  useEffect(() => {
    setSelectedIds((prev) => {
      if (prev.size > 0) setToast("Selection cleared (page changed).");
      return new Set();
    });
  }, [page]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

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
      prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.document_id)),
    );
  }

  async function applyBulkStatus() {
    setBulkBusy(true);
    try {
      const result = await bulkUpdateStatus([...selectedIds], bulkStatus);
      setToast(`Updated ${result.updated} contract(s).`);
      setSelectedIds(new Set());
      await load();
    } catch {
      setToast("Bulk update failed.");
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

  async function onExportPortfolio() {
    setExportingPortfolio(true);
    try {
      await downloadPdf("/api/portfolio/export/pdf", "portfolio_report.pdf");
    } finally {
      setExportingPortfolio(false);
    }
  }

  async function onExportRow(documentId: string, vendor: string) {
    await downloadPdf(
      `/api/documents/${encodeURIComponent(documentId)}/export/pdf`,
      `audit_${vendor}.pdf`,
    );
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h1 style={{ margin: 0 }}>Portfolio dashboard</h1>
        <button
          className="btn"
          style={{ marginLeft: "auto" }}
          onClick={onExportPortfolio}
          disabled={exportingPortfolio}
        >
          {exportingPortfolio ? "Generating…" : "Export Portfolio Report"}
        </button>
      </div>

      {summary ? (
        <div className="stat-row">
          <StatCard label="Total Contracts" value={summary.total_contracts} />
          <StatCard
            label="Avg Risk Score"
            value={summary.avg_risk_score}
            tone={avgBand(summary.avg_risk_score)}
          />
          <StatCard
            label="High Risk"
            value={summary.risk_distribution.high}
            tone="high"
          />
          <StatCard
            label="Auto-Renewal Active"
            value={summary.contracts_with_autorenewal}
          />
          <StatCard
            label="Expiring Soon (60d)"
            value={summary.contracts_expiring_soon}
            tone={
              summary.contracts_expiring_soon > 3
                ? "high"
                : summary.contracts_expiring_soon > 0
                  ? "medium"
                  : undefined
            }
          />
        </div>
      ) : null}

      <div className="filter-bar">
        <Select label="Risk" value={risk} onChange={(v) => setRisk(v as RiskLevel)}
          options={[["all", "All"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]]} />
        <Select label="Auto-renewal" value={autoRenewal}
          onChange={(v) => setAutoRenewal(v as "all" | "yes" | "no")}
          options={[["all", "All"], ["yes", "Yes"], ["no", "No"]]} />
        <Select label="Status" value={statusFilter} onChange={setStatusFilter}
          options={[["", "All"], ["audited", "Audited"], ["reviewed", "Reviewed"], ["approved", "Approved"], ["flagged", "Flagged"]]} />
        <Select label="Sort by" value={sortBy ?? "created_at"}
          onChange={(v) => setSortBy(v as ContractFilters["sort_by"])}
          options={[["created_at", "Upload date"], ["risk_score", "Risk score"], ["vendor_name", "Vendor name"]]} />
      </div>

      <table className="dev-table">
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                checked={rows.length > 0 && selectedIds.size === rows.length}
                onChange={toggleAll}
                aria-label="Select all on this page"
              />
            </th>
            <th>Vendor</th><th>Type</th><th>Risk</th><th>Auto-Renewal</th>
            <th>Notice</th><th>End Date</th><th>Status</th><th>Uploaded</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.document_id} className="clickable" onClick={() => setSelected(r)}>
              <td onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(r.document_id)}
                  onChange={() => toggleRow(r.document_id)}
                  aria-label={`Select ${r.vendor_name}`}
                />
              </td>
              <td>{r.vendor_name}</td>
              <td>{r.contract_type}</td>
              <td><RiskBadge score={r.risk_score} /></td>
              <td>{r.auto_renewal ? "Yes" : "No"}</td>
              <td>{r.notice_period_days ?? "—"}</td>
              <td>{formatDate(r.contract_end_date)}</td>
              <td><span className={`pill status--${r.status}`}>{r.status}</span></td>
              <td>{formatDate(r.created_at)}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <button className="link-btn" onClick={() => onExportRow(r.document_id, r.vendor_name)}>
                  Export
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr><td colSpan={10} className="muted">No contracts match these filters.</td></tr>
          ) : null}
        </tbody>
      </table>

      <div className="pager">
        <button className="link-btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          ← Previous
        </button>
        <span className="muted">Page {page} of {totalPages}</span>
        <button className="link-btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
          Next →
        </button>
        <Select label="Per page" value={String(pageSize)}
          onChange={(v) => { setPageSize(Number(v)); setPage(1); }}
          options={[["10", "10"], ["20", "20"], ["50", "50"]]} />
      </div>

      {selectedIds.size > 0 ? (
        <div className="bulk-bar">
          <span>{selectedIds.size} contracts selected</span>
          <select
            value={bulkStatus}
            onChange={(e) => setBulkStatus(e.target.value as WorkflowStatus)}
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => void applyBulkStatus()} disabled={bulkBusy}>
            Apply
          </button>
          <button className="btn" onClick={() => void exportSelected()} disabled={bulkBusy}>
            Export Selected
          </button>
          <button className="link-btn" onClick={() => setSelectedIds(new Set())}>
            Clear selection
          </button>
          {bulkBusy ? <span className="muted">Working…</span> : null}
        </div>
      ) : null}

      {toast ? <div className="toast toast--ok">{toast}</div> : null}

      <ContractPanel contract={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "low" | "medium" | "high";
}) {
  return (
    <div className={`stat-card${tone ? ` stat-card--${tone}` : ""}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}
