/**
 * Browser-side API client.
 *
 * Calls the Next.js same-origin proxy routes (`/api/**`) — never the FastAPI
 * service directly. The proxy attaches the backend API key server-side, so no
 * credential is exposed to the browser, and the frontend stays free of business
 * logic (Architectural Constraint #1).
 */

import type {
  ContractAudit,
  QARequest,
  QAResponse,
  StatusResponse,
  UploadResponse,
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
