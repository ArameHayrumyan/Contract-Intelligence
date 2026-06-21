"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ActivityTimeline } from "@/components/ActivityTimeline";
import { AnnotationPanel } from "@/components/AnnotationPanel";
import {
  cleanVendor,
  daysUntil,
  endDateClass,
  formatDate,
} from "@/components/ContractPanel";
import { DeviationTable } from "@/components/DeviationTable";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import {
  downloadPdf,
  getAudit,
  getDocumentActivity,
  listContracts,
  listStandards,
  runCrossReference,
  updateContractStatus,
} from "@/lib/api-client";
import type {
  ActivityEntry,
  ContractAudit,
  ContractRow,
  CrossReferenceAudit,
  StandardGroup,
  WorkflowStatus,
} from "@/lib/types";

type Tab = "overview" | "clauses" | "crossref" | "activity";
const STATUSES: WorkflowStatus[] = ["audited", "reviewed", "approved", "flagged"];

export default function AuditDetailPage() {
  const params = useParams<{ documentId: string }>();
  const documentId = params.documentId;
  const router = useRouter();
  const { toast } = useToast();

  const [audit, setAudit] = useState<ContractAudit | null>(null);
  const [row, setRow] = useState<ContractRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [a, page] = await Promise.all([
        getAudit(documentId),
        listContracts({ page: 1, page_size: 1000 }),
      ]);
      setAudit(a);
      setRow(page.items.find((r) => r.document_id === documentId) ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load audit.");
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Deep-link to a specific tab via ?tab= (e.g. from the dashboard menu).
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t === "overview" || t === "clauses" || t === "crossref" || t === "activity") {
      setTab(t);
    }
  }, []);

  if (error) return <div className="container"><p className="error">{error}</p></div>;
  if (!audit) {
    return (
      <div className="container">
        <Skeleton height={32} width="40%" />
        <div style={{ marginTop: "var(--space-4)" }}>
          <Skeleton height={200} borderRadius="var(--radius-lg)" />
        </div>
      </div>
    );
  }

  const vendor = cleanVendor(audit.vendor_name) ?? "Unnamed Contract";

  return (
    <div className="container--wide">
      <PageHeader
        title={vendor}
        subtitle={`${cleanVendor(audit.contract_type) ?? "—"} · ${audit.critical_clauses.length} critical clauses`}
        backHref="/dashboard"
        backLabel="← Dashboard"
        actions={
          <>
            <Badge kind="risk" score={audit.risk_score} />
            <Button
              variant="secondary"
              onClick={() =>
                downloadPdf(
                  `/api/documents/${encodeURIComponent(documentId)}/export/pdf`,
                  `audit_${vendor}.pdf`,
                )
              }
            >
              Export PDF
            </Button>
          </>
        }
      />

      <div className="detail-grid">
        <div>
          <div className="tab-bar">
            {(
              [
                ["overview", "Overview"],
                ["clauses", "Critical Clauses"],
                ["crossref", "Cross-Reference"],
                ["activity", "Activity"],
              ] as [Tab, string][]
            ).map(([key, lbl]) => (
              <button
                key={key}
                className={`tab${tab === key ? " is-active" : ""}`}
                onClick={() => setTab(key)}
              >
                {lbl}
              </button>
            ))}
          </div>

          {tab === "overview" ? (
            <OverviewTab audit={audit} documentId={documentId} />
          ) : null}
          {tab === "clauses" ? (
            <ClausesTab audit={audit} documentId={documentId} />
          ) : null}
          {tab === "crossref" ? (
            <CrossRefTab documentId={documentId} hasCrossref={row?.has_crossref ?? false} />
          ) : null}
          {tab === "activity" ? (
            <ActivityTab documentId={documentId} vendor={vendor} />
          ) : null}
        </div>

        <div className="detail-sidebar">
          <QuickFacts audit={audit} />
          <ReviewerStatus row={row} documentId={documentId} onSaved={load} toast={toast} />
          <Card title="Quick Actions">
            <div className="stack" style={{ gap: "var(--space-2)" }}>
              <button className="link-btn" onClick={() => setTab("crossref")}>
                Run Cross-Reference →
              </button>
              <button
                className="link-btn"
                onClick={() =>
                  downloadPdf(
                    `/api/documents/${encodeURIComponent(documentId)}/export/pdf`,
                    `audit_${vendor}.pdf`,
                  )
                }
              >
                Export PDF →
              </button>
              <button
                className="link-btn"
                onClick={() => router.push(`/qa?doc=${encodeURIComponent(documentId)}`)}
              >
                Ask about this contract →
              </button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function OverviewTab({ audit, documentId }: { audit: ContractAudit; documentId: string }) {
  return (
    <div className="stack">
      <Card title="Contract Details">
        <dl className="kv-grid">
          <dt>Vendor</dt>
          <dd>{cleanVendor(audit.vendor_name) ?? <span className="cell-empty">—</span>}</dd>
          <dt>Contract Type</dt>
          <dd>{cleanVendor(audit.contract_type) ?? <span className="cell-empty">—</span>}</dd>
          <dt>Auto-Renewal</dt>
          <dd>{audit.auto_renewal ? "Yes" : "No"}</dd>
          <dt>Notice Period</dt>
          <dd>{audit.notice_period_days} days</dd>
          <dt>End Date</dt>
          <dd className={endDateClass(audit.contract_end_date)}>
            {formatDate(audit.contract_end_date)}
          </dd>
          <dt>Liability Cap</dt>
          <dd>{audit.liability_cap_description}</dd>
        </dl>
      </Card>
      <Card title="Risk Rationale">
        <p style={{ lineHeight: 1.7 }}>{audit.risk_rationale}</p>
      </Card>
      <Card title="Reviewer Notes">
        <AnnotationPanel documentId={documentId} targetType="document" />
      </Card>
    </div>
  );
}

function ClausesTab({ audit, documentId }: { audit: ContractAudit; documentId: string }) {
  const { toast } = useToast();
  const [open, setOpen] = useState<string | null>(null);
  if (audit.critical_clauses.length === 0) {
    return <EmptyState title="No critical clauses" description="None were identified for this contract." />;
  }
  return (
    <div className="stack">
      {audit.critical_clauses.map((c, i) => {
        const isOpen = open === c.source_chunk_id;
        return (
          <Card key={`${c.source_chunk_id}-${i}`}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
                Page {c.page_number ?? "?"}
              </span>
              <button
                className="copy-btn"
                onClick={() => {
                  void navigator.clipboard?.writeText(c.source_chunk_id);
                  toast("Chunk ID copied.", "success");
                }}
              >
                {c.source_chunk_id.slice(0, 10)}… ⧉
              </button>
            </div>
            <p className="clause-card__text" style={{ marginTop: "var(--space-2)" }}>
              {c.text}
            </p>
            <div className="card__footer">
              <button className="link-btn" onClick={() => setOpen(isOpen ? null : c.source_chunk_id)}>
                {isOpen ? "Hide annotations" : "Annotations"}
              </button>
              {isOpen ? (
                <AnnotationPanel
                  documentId={documentId}
                  targetType="clause"
                  targetReference={c.source_chunk_id}
                />
              ) : null}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function CrossRefTab({ documentId, hasCrossref }: { documentId: string; hasCrossref: boolean }) {
  const { toast } = useToast();
  const [groups, setGroups] = useState<StandardGroup[]>([]);
  const [selected, setSelected] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<CrossReferenceAudit | null>(null);

  useEffect(() => {
    listStandards().then(setGroups).catch(() => setGroups([]));
  }, []);

  const options = groups.flatMap((g) =>
    g.versions
      .filter((v) => v.status === "ready")
      .map((v) => ({ id: v.standard_document_id, label: `${g.standard_name} — ${v.standard_version}` })),
  );

  async function run() {
    if (!selected) return;
    setRunning(true);
    try {
      setResult(await runCrossReference(documentId, selected));
    } catch (err) {
      toast(err instanceof Error ? err.message : "Cross-reference failed.", "error");
    } finally {
      setRunning(false);
    }
  }

  if (!result) {
    return (
      <Card>
        {hasCrossref ? (
          <p className="muted">
            A cross-reference was previously run. Run again below to view the latest result.
          </p>
        ) : (
          <EmptyState title="No cross-reference audit run yet" />
        )}
        <div className="stack" style={{ marginTop: "var(--space-4)" }}>
          <select className="select" value={selected} onChange={(e) => setSelected(e.target.value)}>
            <option value="">Select a standard…</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </select>
          <Button onClick={run} loading={running} disabled={!selected}>
            Run Cross-Reference
          </Button>
          <p className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
            Compares this contract against your selected corporate standard and may
            take 15–30 seconds.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <div className="stack">
      <Card
        title={
          <span className="row">
            Result <Badge kind="risk" score={result.overall_risk_score} />
          </span>
        }
      >
        <p className="text-muted" style={{ fontSize: "var(--text-sm)" }}>
          vs. standard {result.standard_version}
        </p>
        <p style={{ whiteSpace: "pre-wrap", marginTop: "var(--space-2)" }}>
          {result.executive_summary}
        </p>
      </Card>
      <Card title="Deviations">
        <DeviationTable deviations={result.deviations} documentId={documentId} />
      </Card>
    </div>
  );
}

function ActivityTab({ documentId, vendor }: { documentId: string; vendor: string }) {
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);
  useEffect(() => {
    getDocumentActivity(documentId)
      .then((p) => setEntries(p.items))
      .catch(() => setEntries([]));
  }, [documentId]);
  if (entries === null) return <Skeleton height={120} />;
  return <ActivityTimeline entries={entries} vendorById={{ [documentId]: vendor }} />;
}

function QuickFacts({ audit }: { audit: ContractAudit }) {
  const days = daysUntil(audit.contract_end_date);
  return (
    <Card title="Quick Facts">
      <dl className="kv-grid">
        <dt>Auto-Renewal</dt>
        <dd>{audit.auto_renewal ? "Yes" : "No"}</dd>
        <dt>Notice Period</dt>
        <dd>{audit.notice_period_days} days</dd>
        <dt>End Date</dt>
        <dd className={endDateClass(audit.contract_end_date)}>
          {formatDate(audit.contract_end_date)}
        </dd>
        <dt>Type</dt>
        <dd>{cleanVendor(audit.contract_type) ?? "—"}</dd>
      </dl>
      {days !== null && days >= 0 && days <= 60 ? (
        <div className="warning-row" style={{ marginTop: "var(--space-3)" }}>
          Expires in {days} days — review renewal terms.
        </div>
      ) : null}
    </Card>
  );
}

function ReviewerStatus({
  row,
  documentId,
  onSaved,
  toast,
}: {
  row: ContractRow | null;
  documentId: string;
  onSaved: () => void;
  toast: (m: string, v?: "success" | "error" | "warning" | "info") => void;
}) {
  const [status, setStatus] = useState<WorkflowStatus>("audited");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (row) setStatus(row.status);
  }, [row]);

  async function save() {
    setBusy(true);
    try {
      await updateContractStatus(documentId, status, note || undefined);
      toast("Status updated.", "success");
      setNote("");
      onSaved();
    } catch {
      toast("Could not update status.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Reviewer Status">
      <div className="stack" style={{ gap: "var(--space-2)" }}>
        <Badge kind="status" status={row?.status ?? "audited"} />
        <select
          className="select"
          value={status}
          onChange={(e) => setStatus(e.target.value as WorkflowStatus)}
        >
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <textarea
          className="textarea"
          placeholder="Optional note (max 500)"
          maxLength={500}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="annotation-form__foot">
          <span />
          <span className="muted">{note.length} / 500</span>
        </div>
        <Button size="sm" onClick={save} loading={busy}>Save Status</Button>
      </div>
    </Card>
  );
}
