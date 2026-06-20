"use client";

import { useCallback, useEffect, useState } from "react";

import { ContractPanel, formatDate } from "@/components/ContractPanel";
import { RiskBadge } from "@/components/RiskBadge";
import { getRenewals, getThresholds, saveThresholds } from "@/lib/api-client";
import type { ContractRow, RenewalReport } from "@/lib/types";

const WINDOW_TONES = ["high", "medium", "low"] as const;

/** SLA & renewal monitoring: configurable windows + unknown-date risks. */
export default function MonitoringPage() {
  const [report, setReport] = useState<RenewalReport | null>(null);
  const [thresholds, setThresholds] = useState<number[]>([30, 60, 90]);
  const [draft, setDraft] = useState<number[]>([30, 60, 90]);
  const [selected, setSelected] = useState<ContractRow | null>(null);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date>(new Date());

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
      .catch(() => setToast({ msg: "Could not load monitoring data.", ok: false }));
  }, [load]);

  // Auto-refresh every 60 seconds.
  useEffect(() => {
    const timer = setInterval(() => void load(thresholds), 60_000);
    return () => clearInterval(timer);
  }, [load, thresholds]);

  // Auto-dismiss the toast.
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

  async function onSave() {
    const ascending = draft.every((v, i) => i === 0 || v > (draft[i - 1] ?? -Infinity));
    if (!ascending || draft.some((v) => v < 1 || v > 365)) {
      setToast({ msg: "Windows must be ascending values between 1 and 365.", ok: false });
      return;
    }
    try {
      const saved = await saveThresholds(draft);
      setThresholds(saved);
      await load(saved);
      setToast({ msg: "Thresholds saved.", ok: true });
    } catch {
      setToast({ msg: "Failed to save thresholds.", ok: false });
    }
  }

  const allEmpty =
    report?.windows.every((w) => w.count === 0) &&
    (report?.unknown_date?.count ?? 0) === 0;

  return (
    <div>
      <h1>SLA &amp; Renewal Monitoring</h1>
      <p className="muted">Last updated {updatedAt.toLocaleTimeString()}</p>

      <details className="panel">
        <summary>Configure alert windows</summary>
        <div style={{ display: "flex", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
          {draft.map((value, i) => (
            <label key={i} className="filter-field">
              <span>Alert Window {i + 1} (days)</span>
              <input
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
            </label>
          ))}
          <button className="btn" onClick={onSave} style={{ alignSelf: "flex-end" }}>
            Save Thresholds
          </button>
        </div>
      </details>

      {allEmpty ? (
        <div className="panel" style={{ textAlign: "center" }}>
          <p style={{ fontSize: 32, margin: 0 }}>✅</p>
          <p>All monitored contracts are outside alert windows.</p>
        </div>
      ) : (
        <div className="window-grid">
          {report?.windows.map((win, i) => (
            <div key={win.threshold_days} className={`panel window--${WINDOW_TONES[i] ?? "low"}`}>
              <h3>Within {win.threshold_days} Days</h3>
              <div className="stat-value">{win.count}</div>
              {win.contracts.length === 0 ? (
                <p className="muted">No contracts in this window</p>
              ) : (
                win.contracts.map((c) => (
                  <button
                    key={c.document_id}
                    className="renewal-item"
                    onClick={() => setSelected(c)}
                  >
                    <strong>{c.vendor_name}</strong>
                    <span>Expires {formatDate(c.contract_end_date)}</span>
                    <span className="muted">Notice: {c.notice_period_days ?? "—"} days</span>
                    <RiskBadge score={c.risk_score} />
                  </button>
                ))
              )}
            </div>
          ))}
        </div>
      )}

      <details className="panel" open={(report?.unknown_date?.count ?? 0) > 0}>
        <summary>
          Auto-Renewal Active — End Date Unknown ({report?.unknown_date?.count ?? 0})
        </summary>
        {(report?.unknown_date?.count ?? 0) > 0 ? (
          <>
            <p className="warning-banner">
              These contracts auto-renew but have no recorded end date. Review each
              contract to verify the renewal date.
            </p>
            {report?.unknown_date?.contracts.map((c) => (
              <button
                key={c.document_id}
                className="renewal-item"
                onClick={() => setSelected(c)}
              >
                <strong>{c.vendor_name}</strong>
                <span className="muted">End date not extracted from contract.</span>
              </button>
            ))}
          </>
        ) : null}
      </details>

      {toast ? (
        <div className={`toast toast--${toast.ok ? "ok" : "err"}`}>{toast.msg}</div>
      ) : null}

      <ContractPanel contract={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
