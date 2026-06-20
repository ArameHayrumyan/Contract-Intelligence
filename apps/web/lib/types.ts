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
  contract_end_date: string | null;
  risk_score: number;
  risk_rationale: string;
  critical_clauses: CriticalClause[];
}

// --- Dashboard / persistence -----------------------------------------------

export type WorkflowStatus =
  | "processing"
  | "audited"
  | "reviewed"
  | "approved"
  | "flagged";

export interface DashboardSummary {
  total_contracts: number;
  avg_risk_score: number;
  risk_distribution: { low: number; medium: number; high: number };
  contracts_with_autorenewal: number;
  contracts_expiring_soon: number;
  recent_high_risk: {
    document_id: string;
    vendor_name: string;
    risk_score: number;
    created_at: string;
  }[];
}

export interface ContractRow {
  document_id: string;
  vendor_name: string;
  contract_type: string;
  risk_score: number;
  auto_renewal: boolean;
  notice_period_days: number | null;
  contract_end_date: string | null;
  status: WorkflowStatus;
  status_note: string | null;
  risk_rationale: string;
  critical_clauses: CriticalClause[];
  has_crossref: boolean;
  created_at: string;
}

export interface ContractPage {
  items: ContractRow[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ContractFilters {
  risk_score_min?: number;
  risk_score_max?: number;
  contract_type?: string;
  auto_renewal?: boolean;
  status?: WorkflowStatus;
  sort_by?: "risk_score" | "created_at" | "vendor_name" | "contract_end_date";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

// --- Monitoring ------------------------------------------------------------

export interface RenewalWindow {
  label: string;
  threshold_days: number;
  count: number;
  contracts: ContractRow[];
}

export interface RenewalReport {
  windows: RenewalWindow[];
  total_at_risk: number;
  configured_thresholds: number[];
  unknown_date?: { label: string; count: number; contracts: ContractRow[] };
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

// --- Cross-reference (contract vs. corporate standard) ---------------------

export type DeviationType =
  | "missing"
  | "weakened"
  | "strengthened"
  | "contradictory"
  | "unaddressed";

export interface StandardVersion {
  standard_document_id: string;
  standard_version: string;
  status: string;
  chunk_count: number | null;
  error: string | null;
}

export interface StandardGroup {
  standard_name: string;
  versions: StandardVersion[];
}

export interface StandardUploadResponse {
  standard_document_id: string;
  standard_name: string;
  standard_version: string;
  status: string;
}

export interface ClauseDeviation {
  clause_type: string;
  subject_text: string;
  subject_chunk_id: string;
  subject_page: number | null;
  standard_text: string | null;
  standard_chunk_id: string | null;
  standard_page: number | null;
  deviation_type: DeviationType;
  severity: number;
  explanation: string;
}

export interface CrossReferenceAudit {
  subject_document_id: string;
  standard_document_id: string;
  standard_version: string;
  deviations: ClauseDeviation[];
  overall_risk_score: number;
  executive_summary: string;
  tenant_id: string;
}

export interface QARequest {
  question: string;
  document_ids?: string[] | null;
}
