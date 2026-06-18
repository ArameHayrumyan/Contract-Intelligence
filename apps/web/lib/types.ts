/**
 * Shared API types mirroring the FastAPI / rag_core schemas.
 *
 * These are kept in lockstep with `packages/rag_core/rag_core/schemas.py`.
 * The frontend holds no business logic — these are pure data shapes.
 */

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface UploadResponse {
  document_id: string;
  status: DocumentStatus;
  filename: string;
  page_count: number;
}

export interface StatusResponse {
  document_id: string;
  status: DocumentStatus;
  chunk_count: number | null;
  error: string | null;
}

export interface CriticalClause {
  text: string;
  source_chunk_id: string;
  page_number: number | null;
  category: string | null;
}

export interface ContractAudit {
  vendor_name: string;
  contract_type: string;
  auto_renewal: boolean;
  notice_period_days: number;
  liability_cap_description: string;
  risk_score: number;
  risk_rationale: string;
  critical_clauses: CriticalClause[];
}

export interface QACitation {
  chunk_id: string;
  document_id: string;
  page_number: number | null;
  snippet: string;
}

export interface QAResponse {
  answer: string;
  citations: QACitation[];
}

export interface QARequest {
  question: string;
  document_ids?: string[] | null;
}
