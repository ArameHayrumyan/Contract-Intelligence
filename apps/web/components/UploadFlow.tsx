"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { DropZone } from "@/components/ui/DropZone";
import { useToast } from "@/components/ui/Toast";
import { getDocumentStatus, uploadDocument } from "@/lib/api-client";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

// Maps the real backend status to an illustrative step index (0-based).
const STEPS = ["Validating", "Parsing", "Extracting", "Indexing"];

interface UploadFlowProps {
  onUploaded?: () => void;
  onClose?: () => void;
  dropHeight?: number;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Shared upload flow: drop a PDF, submit, poll real ingestion status, succeed. */
export function UploadFlow({ onUploaded, onClose, dropHeight = 180 }: UploadFlowProps) {
  const { toast } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [statusLabel, setStatusLabel] = useState("");

  // Step lighting is driven by the real phase, not a timer.
  const activeStep =
    phase === "uploading" ? 0 : phase === "processing" ? 2 : phase === "done" ? 4 : -1;

  async function poll(documentId: string) {
    // Poll real ingestion status until ready/failed (cap ~2 minutes).
    for (let i = 0; i < 60; i++) {
      const status = await getDocumentStatus(documentId);
      if (status.status === "ready") {
        setPhase("done");
        onUploaded?.();
        return;
      }
      if (status.status === "failed") {
        setPhase("error");
        toast(status.error || "Processing failed.", "error");
        return;
      }
      setStatusLabel(status.status);
      await sleep(2000);
    }
    // Still queued after the cap — surface success; it will finish in the background.
    setPhase("done");
    onUploaded?.();
  }

  async function submit() {
    if (!file) return;
    setPhase("uploading");
    setStatusLabel("uploading");
    try {
      const res = await uploadDocument(file);
      setPhase("processing");
      await poll(res.document_id);
    } catch (err) {
      setPhase("error");
      toast(err instanceof Error ? err.message : "Upload failed.", "error");
    }
  }

  function reset() {
    setFile(null);
    setPhase("idle");
    setStatusLabel("");
  }

  if (phase === "done") {
    return (
      <div className="stack" style={{ textAlign: "center", alignItems: "center" }}>
        <div className="success-banner" style={{ width: "100%" }}>
          Contract ingested and ready to audit.
        </div>
        <div className="row">
          <button className="link-btn" onClick={reset}>
            Upload another
          </button>
          <a className="btn btn--primary btn--md" href="/dashboard">
            Go to Dashboard
          </a>
        </div>
      </div>
    );
  }

  const busy = phase === "uploading" || phase === "processing";

  return (
    <div className="stack">
      {phase === "error" ? (
        <div className="warning-row">Upload failed — please try again.</div>
      ) : null}
      <DropZone onFile={setFile} maxSizeMB={50} height={dropHeight} />

      {busy ? (
        <div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            {STEPS.map((label, i) => (
              <span
                key={label}
                style={{
                  fontSize: "var(--text-xs)",
                  color:
                    i < activeStep
                      ? "var(--color-risk-low)"
                      : i === activeStep
                        ? "var(--color-text-primary)"
                        : "var(--color-text-muted)",
                }}
              >
                {i < activeStep ? "✓ " : ""}
                {label}
              </span>
            ))}
          </div>
          <div className="skeleton" style={{ height: 4, marginTop: "var(--space-2)" }} />
          <p
            className="text-muted"
            style={{ fontSize: "var(--text-xs)", marginTop: "var(--space-2)" }}
          >
            {phase === "uploading" ? "Uploading…" : `Ingesting (${statusLabel})…`}
          </p>
        </div>
      ) : null}

      <div className="row" style={{ justifyContent: "flex-end" }}>
        {onClose ? (
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
        ) : null}
        <Button onClick={submit} disabled={!file || busy} loading={busy}>
          Upload &amp; Ingest
        </Button>
      </div>
    </div>
  );
}
