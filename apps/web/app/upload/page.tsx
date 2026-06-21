"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { cleanVendor, formatDate } from "@/components/ContractPanel";
import { UploadFlow } from "@/components/UploadFlow";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { listContracts } from "@/lib/api-client";
import type { ContractRow } from "@/lib/types";

export default function UploadPage() {
  const [recent, setRecent] = useState<ContractRow[] | null>(null);

  const loadRecent = useCallback(async () => {
    const page = await listContracts({
      page: 1,
      page_size: 5,
      sort_by: "created_at",
      sort_order: "desc",
    });
    setRecent(page.items);
  }, []);

  useEffect(() => {
    void loadRecent();
  }, [loadRecent]);

  return (
    <div className="container">
      <PageHeader
        title="Upload a Contract"
        subtitle="PDF only · Max 50MB · Validated before ingestion"
      />

      <Card>
        <UploadFlow dropHeight={240} onUploaded={loadRecent} />
      </Card>

      <div style={{ marginTop: "var(--space-6)" }}>
        <h2 style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-3)" }}>
          Recent Uploads
        </h2>
        {recent === null ? (
          <Skeleton height={120} borderRadius="var(--radius-lg)" />
        ) : recent.length === 0 ? (
          <EmptyState title="Nothing yet" description="Your recent uploads will appear here." />
        ) : (
          <Card>
            {recent.map((r) => (
              <Link
                key={r.document_id}
                href={`/audit/${r.document_id}`}
                className="row"
                style={{
                  justifyContent: "space-between",
                  padding: "var(--space-2) 0",
                  borderBottom: "1px solid var(--color-border-subtle)",
                  color: "var(--color-text-primary)",
                }}
              >
                <span className="row">
                  <FileIcon />
                  <span>{cleanVendor(r.vendor_name) ?? "Unnamed Contract"}</span>
                </span>
                <span className="row">
                  <Badge kind="risk" score={r.risk_score} size="xs" />
                  <Badge kind="status" status={r.status} size="xs" />
                  <span className="text-muted">{formatDate(r.created_at)}</span>
                </span>
              </Link>
            ))}
          </Card>
        )}
      </div>
    </div>
  );
}

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M14 3H6v18h12V8z M14 3v5h4" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
