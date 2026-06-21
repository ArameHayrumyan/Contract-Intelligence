"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DropZone } from "@/components/ui/DropZone";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { listStandards, uploadStandard } from "@/lib/api-client";
import type { StandardGroup } from "@/lib/types";

export default function StandardsPage() {
  const { toast } = useToast();
  const [groups, setGroups] = useState<StandardGroup[] | null>(null);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dzKey, setDzKey] = useState(0); // bump to remount/clear the DropZone
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setGroups(await listStandards());
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    if (!name || !version || !file) return;
    setBusy(true);
    try {
      await uploadStandard(name, version, file);
      toast("Standard uploaded.", "success");
      setName("");
      setVersion("");
      setFile(null);
      setDzKey((k) => k + 1);
      await load();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Upload failed.", "error");
    } finally {
      setBusy(false);
    }
  }

  const count = groups?.reduce((n, g) => n + g.versions.length, 0) ?? 0;

  return (
    <div className="container">
      <PageHeader
        title="Corporate Standards"
        subtitle="Policy documents used for cross-reference auditing. Append-only — new uploads create versions."
      />

      <div className="two-col">
        <Card title="Upload a Standard">
          <div className="stack">
            <DropZone key={dzKey} onFile={setFile} maxSizeMB={50} height={160} />
            <div>
              <label className="field-label">Standard Name</label>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Vendor Contract Policy"
              />
            </div>
            <div>
              <label className="field-label">Version</label>
              <input
                className="input"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="2025.1"
              />
            </div>
            <Button
              fullWidth
              onClick={submit}
              loading={busy}
              disabled={!name || !version || !file}
            >
              Upload Standard
            </Button>
          </div>
        </Card>

        <Card
          title={
            <span className="row">
              Uploaded Standards <span className="count-badge">{count}</span>
            </span>
          }
        >
          {groups === null ? (
            <Skeleton height={120} />
          ) : groups.length === 0 ? (
            <EmptyState
              title="No standards uploaded yet"
              description="Upload a standard to enable cross-reference auditing."
            />
          ) : (
            groups.map((g, gi) => (
              <div
                key={g.standard_name}
                style={{
                  paddingBottom: "var(--space-3)",
                  marginBottom: "var(--space-3)",
                  borderBottom:
                    gi < groups.length - 1
                      ? "1px solid var(--color-border-subtle)"
                      : "none",
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: "var(--space-2)" }}>
                  {g.standard_name}
                </div>
                {g.versions.map((v) => (
                  <div
                    key={v.standard_document_id}
                    className="row"
                    style={{
                      justifyContent: "space-between",
                      padding: "var(--space-1) 0",
                    }}
                  >
                    <span className="mono text-muted">{v.standard_version}</span>
                    <span className="row">
                      <Badge kind="status" status={v.status} size="xs" />
                      <Link href="/dashboard">Use in audit →</Link>
                    </span>
                  </div>
                ))}
              </div>
            ))
          )}
        </Card>
      </div>
    </div>
  );
}
