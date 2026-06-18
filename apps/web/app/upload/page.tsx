"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApiError, getDocumentStatus, uploadDocument } from "@/lib/api-client";
import type { DocumentStatus } from "@/lib/types";

interface TrackedDoc {
  documentId: string;
  filename: string;
  status: DocumentStatus;
  error: string | null;
}

/** Upload workflow: submit a PDF, then poll its ingestion status. */
export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doc, setDoc] = useState<TrackedDoc | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Poll status until the document reaches a terminal state.
  useEffect(() => {
    if (!doc || doc.status === "ready" || doc.status === "failed") {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const status = await getDocumentStatus(doc.documentId);
        setDoc((prev) =>
          prev
            ? { ...prev, status: status.status, error: status.error }
            : prev,
        );
      } catch {
        // Transient poll failure; keep trying.
      }
    }, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [doc]);

  async function onUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setDoc(null);
    try {
      const res = await uploadDocument(file);
      setDoc({
        documentId: res.document_id,
        filename: res.filename,
        status: res.status,
        error: null,
      });
    } catch (err) {
      if (err instanceof ApiError) {
        const detail = err.detail as { message?: string } | string | undefined;
        setError(
          typeof detail === "object" && detail?.message
            ? detail.message
            : err.message,
        );
      } else {
        setError("Upload failed.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>Upload a contract</h1>
      <p className="muted">
        PDF only. Files are validated (size, type, page count) before ingestion.
      </p>

      <form className="panel" onSubmit={onUpload}>
        <input
          type="file"
          accept="application/pdf,.pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button
          className="btn"
          type="submit"
          disabled={busy || !file}
          style={{ marginTop: 14 }}
        >
          {busy ? "Uploading…" : "Upload & ingest"}
        </button>
        {error ? <p className="error">{error}</p> : null}
      </form>

      {doc ? (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>{doc.filename}</h3>
          <p>
            Status: <strong>{doc.status}</strong>
            {doc.status === "processing" || doc.status === "pending"
              ? " — ingesting, please wait…"
              : null}
          </p>
          {doc.error ? <p className="error">{doc.error}</p> : null}
          {doc.status === "ready" ? (
            <Link className="btn" href={`/audit/${doc.documentId}`}>
              View audit →
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
