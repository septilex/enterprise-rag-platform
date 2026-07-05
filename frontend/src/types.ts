export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  chunk_index: number;
  snippet: string;
}

export interface Collection {
  id: string;
  tenant_id: string;
  name: string;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  collection_id: string | null;
  user_id: string;
  title: string;
  created_at: string;
}

export interface Me {
  authenticated: boolean;
  superuser: boolean;
  user_id: string | null;
  email: string | null;
  tenants: { tenant_id: string; role: string }[];
}

export interface DocumentSummary {
  id: string;
  collection_id: string;
  source_type: string;
  source_uri: string;
  status: string;
  doc_metadata: Record<string, unknown>;
  created_at: string;
  chunk_count: number;
  source_id: string | null;
}

export interface Member {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AuditEntry {
  id: string;
  tenant_id: string | null;
  actor: string;
  action: string;
  target: Record<string, unknown>;
  created_at: string;
}

export interface Source {
  id: string;
  tenant_id: string;
  collection_id: string | null;
  source_type: string;
  display_name: string;
  external_ref: string | null;
  enabled: boolean;
  last_success_at: string | null;
  last_error_at: string | null;
  created_at: string;
}

export interface SourceHealth {
  id: string;
  display_name: string;
  source_type: string;
  enabled: boolean;
  health: "healthy" | "stale" | "failing" | "idle";
  last_success_at: string | null;
  last_error_at: string | null;
}

export interface SystemStatus {
  api: string;
  worker: { alive: boolean; last_heartbeat_seconds_ago: number | null };
  queue: { incremental: number; bulk: number; dead: number };
  ingestion_total: number;
  ingestion_by_status: Record<string, number>;
  active_runs: number;
  failed_runs: number;
  success_rate: number;
  sources: SourceHealth[];
}

export interface IngestionRun {
  id: string;
  source_id: string;
  collection_id: string | null;
  trigger_type: string;
  status: string;
  documents_seen: number;
  documents_indexed: number;
  documents_quarantined: number;
  documents_deleted: number;
  chunks_created: number;
  chunks_reused: number;
  error_summary: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  grounded?: boolean;
  streaming?: boolean;
}
