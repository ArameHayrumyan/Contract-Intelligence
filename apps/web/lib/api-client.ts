/**
 * Browser-side API client.
 *
 * Calls the Next.js same-origin proxy routes (`/api/**`) — never the FastAPI
 * service directly. The proxy attaches the backend API key server-side, so no
 * credential is exposed to the browser, and the frontend stays free of business
 * logic (Architectural Constraint #1).
 */

import type {
  ActivityPage,
  AnnotationResponse,
  AnnotationTarget,
  AnnotationType,
  BulkStatusResult,
  ContractAudit,
  ContractFilters,
  ContractPage,
  ContractRow,
  CrossReferenceAudit,
  DashboardSummary,
  QARequest,
  QAResponse,
  RenewalReport,
  StandardGroup,
  StandardUploadResponse,
  StatusResponse,
  UploadResponse,
  WorkflowStatus,
} from "@/lib/types";

/** Error carrying the HTTP status and parsed backend detail. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body?.detail ?? body;
    throw new ApiError(
      typeof detail === "string" ? detail : `Request failed (${res.status})`,
      res.status,
      detail,
    );
  }
  return body as T;
}

/** Upload a contract PDF for ingestion. */
export async function uploadDocument(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/documents", { method: "POST", body: form });
  return parse<UploadResponse>(res);
}

/** Poll a document's ingestion status. */
export async function getDocumentStatus(
  documentId: string,
): Promise<StatusResponse> {
  const res = await fetch(`/api/documents/${encodeURIComponent(documentId)}`, {
    cache: "no-store",
  });
  return parse<StatusResponse>(res);
}

/** Fetch the structured audit for a document. */
export async function getAudit(documentId: string): Promise<ContractAudit> {
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/audit`,
    { cache: "no-store" },
  );
  return parse<ContractAudit>(res);
}

/** Ask a cross-document question over the tenant's documents. */
export async function askQuestion(payload: QARequest): Promise<QAResponse> {
  const res = await fetch("/api/qa", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parse<QAResponse>(res);
}

/** Upload a corporate standard (name + version + PDF). */
export async function uploadStandard(
  standardName: string,
  standardVersion: string,
  file: File,
): Promise<StandardUploadResponse> {
  const form = new FormData();
  form.append("standard_name", standardName);
  form.append("standard_version", standardVersion);
  form.append("file", file);
  const res = await fetch("/api/standards", { method: "POST", body: form });
  return parse<StandardUploadResponse>(res);
}

/** List the tenant's standards, grouped by name with all versions. */
export async function listStandards(): Promise<StandardGroup[]> {
  const res = await fetch("/api/standards", { cache: "no-store" });
  return parse<StandardGroup[]>(res);
}

/** Run a cross-reference audit of a document against a standard. */
export async function runCrossReference(
  documentId: string,
  standardDocumentId: string,
): Promise<CrossReferenceAudit> {
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/cross-reference`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ standard_document_id: standardDocumentId }),
    },
  );
  return parse<CrossReferenceAudit>(res);
}

// --- Dashboard -------------------------------------------------------------

/** Fetch portfolio summary statistics. */
export async function getDashboardSummary(): Promise<DashboardSummary> {
  const res = await fetch("/api/dashboard/summary", { cache: "no-store" });
  return parse<DashboardSummary>(res);
}

/** Build a query string from contract filters (omitting undefined values). */
function filterQuery(filters: ContractFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  return params.toString();
}

/** Fetch a filtered, paginated page of contracts. */
export async function listContracts(
  filters: ContractFilters,
): Promise<ContractPage> {
  const res = await fetch(`/api/dashboard/contracts?${filterQuery(filters)}`, {
    cache: "no-store",
  });
  return parse<ContractPage>(res);
}

/** Update a contract's workflow status. */
export async function updateContractStatus(
  documentId: string,
  status: WorkflowStatus,
  note?: string,
): Promise<ContractRow> {
  const res = await fetch(
    `/api/dashboard/contracts/${encodeURIComponent(documentId)}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note: note ?? null }),
    },
  );
  return parse<ContractRow>(res);
}

// --- Monitoring ------------------------------------------------------------

/** Fetch renewal alerts grouped into the configured windows. */
export async function getRenewals(
  thresholds: number[],
  includeNoDate: boolean,
): Promise<RenewalReport> {
  const params = new URLSearchParams({
    thresholds: thresholds.join(","),
    include_no_date: String(includeNoDate),
  });
  const res = await fetch(`/api/monitoring/renewals?${params.toString()}`, {
    cache: "no-store",
  });
  return parse<RenewalReport>(res);
}

/** Fetch the tenant's configured renewal thresholds. */
export async function getThresholds(): Promise<number[]> {
  const res = await fetch("/api/monitoring/thresholds", { cache: "no-store" });
  return (await parse<{ thresholds: number[] }>(res)).thresholds;
}

/** Persist new renewal thresholds. */
export async function saveThresholds(thresholds: number[]): Promise<number[]> {
  const res = await fetch("/api/monitoring/thresholds", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thresholds }),
  });
  return (await parse<{ thresholds: number[] }>(res)).thresholds;
}

// --- PDF / zip export ------------------------------------------------------

/**
 * Download a binary file (PDF or zip) from a same-origin proxy route.
 *
 * Works for GET (default) and POST (pass `init` with a method/body) — the
 * MIME type is whatever the proxy streams back, so this handles both
 * application/pdf and application/zip.
 */
export async function downloadPdf(
  url: string,
  filename: string,
  init?: RequestInit,
): Promise<void> {
  const res = await fetch(url, { cache: "no-store", ...init });
  if (!res.ok) {
    throw new ApiError(`Export failed (${res.status})`, res.status);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

// --- Bulk operations -------------------------------------------------------

/** Bulk-update workflow status for many contracts. */
export async function bulkUpdateStatus(
  documentIds: string[],
  status: WorkflowStatus,
  note?: string,
): Promise<BulkStatusResult> {
  const res = await fetch("/api/dashboard/contracts/bulk/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids: documentIds, status, note: note ?? null }),
  });
  return parse<BulkStatusResult>(res);
}

/** Download a zip of audit PDFs for the selected contracts. */
export async function bulkExport(documentIds: string[]): Promise<void> {
  const date = new Date().toISOString().slice(0, 10);
  await downloadPdf(
    "/api/dashboard/contracts/bulk/export",
    `bulk_export_${date}_${documentIds.length}_contracts.zip`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: documentIds }),
    },
  );
}

// --- Annotations -----------------------------------------------------------

/** List a document's annotations, optionally filtered by target. */
export async function listAnnotations(
  documentId: string,
  targetType?: AnnotationTarget,
  targetReference?: string,
): Promise<AnnotationResponse[]> {
  const params = new URLSearchParams();
  if (targetType) params.set("target_type", targetType);
  if (targetReference) params.set("target_reference", targetReference);
  const query = params.toString();
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/annotations${
      query ? `?${query}` : ""
    }`,
    { cache: "no-store" },
  );
  return parse<AnnotationResponse[]>(res);
}

/** Create an annotation on a document / clause / deviation. */
export async function createAnnotation(
  documentId: string,
  body: {
    target_type: AnnotationTarget;
    target_reference?: string | null;
    annotation_type: AnnotationType;
    note: string;
  },
): Promise<AnnotationResponse> {
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/annotations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return parse<AnnotationResponse>(res);
}

/** Edit an annotation's type and note. */
export async function updateAnnotation(
  documentId: string,
  annotationId: string,
  annotationType: AnnotationType,
  note: string,
): Promise<AnnotationResponse> {
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/annotations/${encodeURIComponent(annotationId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ annotation_type: annotationType, note }),
    },
  );
  return parse<AnnotationResponse>(res);
}

/** Soft-delete an annotation. */
export async function deleteAnnotation(
  documentId: string,
  annotationId: string,
): Promise<void> {
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/annotations/${encodeURIComponent(annotationId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new ApiError(`Delete failed (${res.status})`, res.status);
  }
}

// --- Activity log ----------------------------------------------------------

/** Fetch the tenant-wide activity log (paginated). */
export async function getActivity(
  page = 1,
  pageSize = 50,
): Promise<ActivityPage> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  const res = await fetch(`/api/activity?${params.toString()}`, {
    cache: "no-store",
  });
  return parse<ActivityPage>(res);
}

/** Fetch one document's activity (bounded, single page). */
export async function getDocumentActivity(
  documentId: string,
): Promise<ActivityPage> {
  const res = await fetch(
    `/api/documents/${encodeURIComponent(documentId)}/activity`,
    { cache: "no-store" },
  );
  return parse<ActivityPage>(res);
}
