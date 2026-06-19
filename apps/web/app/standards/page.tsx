"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, listStandards, uploadStandard } from "@/lib/api-client";
import type { StandardGroup } from "@/lib/types";

/** Corporate-standards management: append-only upload + versioned listing. */
export default function StandardsPage() {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [groups, setGroups] = useState<StandardGroup[]>([]);

  const refresh = useCallback(async () => {
    try {
      setGroups(await listStandards());
    } catch {
      // Non-fatal; the list simply won't update.
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!file || !name || !version) return;
    setBusy(true);
    setError(null);
    try {
      await uploadStandard(name, version, file);
      setName("");
      setVersion("");
      setFile(null);
      // Give ingestion a moment, then refresh status.
      setTimeout(() => void refresh(), 1500);
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        const detail = err.detail as { message?: string } | string | undefined;
        setError(
          typeof detail === "object" && detail?.message ? detail.message : err.message,
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
      <h1>Corporate standards</h1>
      <p className="muted">
        Upload your policy documents. Standards are versioned and append-only —
        new uploads never overwrite previous versions.
      </p>

      <form className="panel" onSubmit={onUpload}>
        <label>
          Standard name
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Vendor Contract Policy"
          />
        </label>
        <label style={{ display: "block", marginTop: 10 }}>
          Version
          <input
            type="text"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            placeholder="e.g. 2025.1"
          />
        </label>
        <input
          type="file"
          accept="application/pdf,.pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          style={{ marginTop: 10 }}
        />
        <button
          className="btn"
          type="submit"
          disabled={busy || !file || !name || !version}
          style={{ marginTop: 14 }}
        >
          {busy ? "Uploading…" : "Upload standard"}
        </button>
        {error ? <p className="error">{error}</p> : null}
      </form>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Uploaded standards</h3>
        {groups.length === 0 ? (
          <p className="muted">No standards uploaded yet.</p>
        ) : (
          groups.map((g) => (
            <div key={g.standard_name} style={{ marginBottom: 14 }}>
              <strong>{g.standard_name}</strong>
              <table className="dev-table" style={{ marginTop: 6 }}>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Chunks</th>
                  </tr>
                </thead>
                <tbody>
                  {g.versions.map((v) => (
                    <tr key={v.standard_document_id}>
                      <td>{v.standard_version}</td>
                      <td>{v.status}</td>
                      <td>{v.chunk_count ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
