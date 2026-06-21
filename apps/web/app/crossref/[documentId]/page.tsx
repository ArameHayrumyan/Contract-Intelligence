"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { RiskBadge } from "@/components/RiskBadge";
import { DeviationTable } from "@/components/DeviationTable";
import { ApiError, listStandards, runCrossReference } from "@/lib/api-client";
import type { CrossReferenceAudit, StandardGroup } from "@/lib/types";

/** Run and display a cross-reference audit of a contract against a standard. */
export default function CrossReferencePage() {
  const params = useParams<{ documentId: string }>();
  const documentId = params.documentId;

  const [groups, setGroups] = useState<StandardGroup[]>([]);
  const [selected, setSelected] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CrossReferenceAudit | null>(null);

  useEffect(() => {
    listStandards()
      .then(setGroups)
      .catch(() => setError("Could not load standards."));
  }, []);

  async function run() {
    if (!selected) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await runCrossReference(documentId, selected));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cross-reference failed.");
    } finally {
      setRunning(false);
    }
  }

  // Flatten ready versions into selectable options.
  const options = groups.flatMap((g) =>
    g.versions
      .filter((v) => v.status === "ready")
      .map((v) => ({
        id: v.standard_document_id,
        label: `${g.standard_name} — ${v.standard_version}`,
      })),
  );

  return (
    <div>
      <h1>Cross-reference audit</h1>
      <p className="muted">
        Compare this contract against a corporate standard, clause by clause.
      </p>

      <div className="panel">
        <label>
          Standard
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            style={{ width: "100%", marginTop: 6 }}
          >
            <option value="">Select a standard…</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {options.length === 0 ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            No ready standards yet — upload one on the Standards page first.
          </p>
        ) : null}
        <button
          className="btn"
          onClick={run}
          disabled={running || !selected}
          style={{ marginTop: 14 }}
        >
          {running ? "Analyzing clauses…" : "Run cross-reference audit"}
        </button>
        {running ? (
          <p className="muted" style={{ marginTop: 10 }}>
            Extracting clause inventories, aligning against the standard, and
            classifying deviations. This takes 15–30 seconds on the free tier.
          </p>
        ) : null}
        {error ? <p className="error">{error}</p> : null}
      </div>

      {result ? (
        <>
          <div
            style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}
          >
            <h2 style={{ margin: 0 }}>Result</h2>
            <RiskBadge score={result.overall_risk_score} />
            <span className="muted">
              {result.deviations.length} deviation
              {result.deviations.length === 1 ? "" : "s"} · standard{" "}
              {result.standard_version}
            </span>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Executive summary</h3>
            <p style={{ whiteSpace: "pre-wrap" }}>{result.executive_summary}</p>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Deviations</h3>
            <DeviationTable deviations={result.deviations} documentId={documentId} />
          </div>
        </>
      ) : null}
    </div>
  );
}
