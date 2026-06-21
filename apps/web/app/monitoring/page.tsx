"use client";

import { useCallback, useEffect, useState } from "react";

import { cleanVendor, ContractPanel, formatDate } from "@/components/ContractPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { getRenewals, getThresholds, saveThresholds } from "@/lib/api-client";
import type { ContractRow, RenewalReport } from "@/lib/types";

const TONE = ["high", "medium", "low"] as const;

export default function MonitoringPage() {
  const { toast } = useToast();
  const [report, setReport] = useState<RenewalReport | null>(null);
  const [thresholds, setThresholds] = useState<number[]>([30, 60, 90]);
  const [draft, setDraft] = useState<number[]>([30, 60, 90]);
  const [configOpen, setConfigOpen] = useState(false);
  const [selected, setSelected] = useState<ContractRow | null>(null);
  const [updatedAt, setUpdatedAt] = useState(new Date());

  const load = useCallback(async (active: number[]) => {
    const data = await getRenewals(active, true);
    setReport(data);
    setUpdatedAt(new Date());
  }, []);

  useEffect(() => {
    getThresholds()
      .then((t) => {
        setThresholds(t);
        setDraft(t);
        return load(t);
      })
      .catch(() => toast("Could not load monitoring data.", "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timer = setInterval(() => void load(thresholds), 60_000);
    return () => clearInterval(timer);
  }, [load, thresholds]);

  async function saveConfig() {
    const ascending = draft.every((v, i) => i === 0 || v > (draft[i - 1] ?? -Infinity));
    if (!ascending || draft.some((v) => v < 1 || v > 365)) {
      toast("Windows must be ascending values between 1 and 365.", "error");
      return;
    }
    try {
      const saved = await saveThresholds(draft);
      setThresholds(saved);
      await load(saved);
      setConfigOpen(false);
      toast("Thresholds saved.", "success");
    } catch {
      toast("Failed to save thresholds.", "error");
    }
  }

  return (
    <div className="container--wide">
      <PageHeader
        title="SLA & Renewal Monitoring"
        subtitle={`Last updated ${updatedAt.toLocaleTimeString()}`}
        actions={
          <Button variant="secondary" onClick={() => setConfigOpen(true)}>
            Configure Alerts
          </Button>
        }
      />

      {report === null ? (
        <Skeleton height={200} borderRadius="var(--radius-lg)" />
      ) : report.total_at_risk === 0 ? (
        <div className="success-banner">
          <span style={{ fontSize: 24 }}>✓</span>
          All monitored contracts are currently outside alert windows.
        </div>
      ) : (
        <div className="window-grid">
          {report.windows.map((win, i) => (
            <div key={win.threshold_days}>
              <div className="window-col__header">
                <strong>Within {win.threshold_days} Days</strong>
                <span className={`badge badge--risk-${TONE[i] ?? "low"}`}>{win.count}</span>
              </div>
              {win.contracts.length === 0 ? (
                <p className="muted">No contracts in this window</p>
              ) : (
                win.contracts.map((c) => (
                  <RenewalCard key={c.document_id} contract={c} tone={TONE[i] ?? "low"} onClick={() => setSelected(c)} />
                ))
              )}
            </div>
          ))}
        </div>
      )}

      {report?.unknown_date ? (
        <details
          className="card"
          open={report.unknown_date.count > 0}
          style={{ marginTop: "var(--space-6)" }}
        >
          <summary style={{ cursor: "pointer" }}>
            Auto-Renewal Active — End Date Unknown ({report.unknown_date.count})
          </summary>
          {report.unknown_date.count > 0 ? (
            <>
              <div className="warning-row" style={{ margin: "var(--space-3) 0" }}>
                These contracts auto-renew but have no recorded end date.
              </div>
              {report.unknown_date.contracts.map((c) => (
                <RenewalCard key={c.document_id} contract={c} tone="low" onClick={() => setSelected(c)} />
              ))}
            </>
          ) : null}
        </details>
      ) : null}

      <Modal
        isOpen={configOpen}
        onClose={() => setConfigOpen(false)}
        title="Configure Alert Windows"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfigOpen(false)}>Cancel</Button>
            <Button onClick={saveConfig}>Save</Button>
          </>
        }
      >
        <div className="stack">
          {draft.map((value, i) => (
            <div key={i}>
              <label className="field-label">Alert Window {i + 1} (days)</label>
              <input
                className="input"
                type="number"
                min={1}
                max={365}
                value={value}
                onChange={(e) => {
                  const next = [...draft];
                  next[i] = Number(e.target.value);
                  setDraft(next);
                }}
              />
            </div>
          ))}
        </div>
      </Modal>

      <ContractPanel contract={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function RenewalCard({
  contract,
  tone,
  onClick,
}: {
  contract: ContractRow;
  tone: "high" | "medium" | "low";
  onClick: () => void;
}) {
  return (
    <div className={`renewal-card renewal-card--${tone}`} onClick={onClick}>
      <span style={{ fontWeight: 500 }}>{cleanVendor(contract.vendor_name) ?? "—"}</span>
      <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
        Expires {formatDate(contract.contract_end_date)}
      </span>
      <span className="text-muted" style={{ fontSize: "var(--text-xs)" }}>
        Notice: {contract.notice_period_days ?? "—"} days
      </span>
      <Badge kind="risk" score={contract.risk_score} size="xs" />
    </div>
  );
}
