export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

// --- Types -----------------------------------------------------------------

export interface IncidentListItem {
  id: number;
  status: string;
  sentry_issue_id: string | null;
  title: string | null;
  confidence: number | null;
  slack_channel_id: string | null;
  slack_thread_ts: string | null;
  created_at: string;
}

export interface EvidenceItem {
  source_type: "SENTRY" | "SLACK" | "CODE" | string;
  source_reference: string;
  explanation: string;
  is_valid: boolean;
  metadata: Record<string, unknown>;
}

export interface InvestigationSummary {
  id: number;
  summary: string | null;
  root_cause: string | null;
  suggested_fix: string | null;
  confidence: number | null;
  analysis_status?: string | null;
  unknowns: string[];
  affected_components?: string[] | null;
  created_at: string;
  completed_at: string | null;
  llm_model?: string | null;
  embedding_model?: string | null;
  slack_latency_ms?: number | null;
  sentry_latency_ms?: number | null;
  retrieval_latency_ms?: number | null;
  llm_latency_ms?: number | null;
  validation_latency_ms?: number | null;
  total_latency_ms?: number | null;
}

export interface RetrievalTrace {
  query?: { semantic_query?: string; exact_filenames?: string[]; exact_symbols?: string[]; identifiers?: string[] } | null;
  lexical_results?: Array<{ rank: number; path: string; start_line: number; end_line: number; symbol?: string | null; score?: number; match_reason?: string }> | null;
  vector_results?: Array<{ rank: number; path: string; start_line: number; end_line: number; symbol?: string | null; score?: number; similarity?: number; match_reason?: string }> | null;
  final_context?: Array<{ rank: number; path: string; start_line: number; end_line: number; symbol?: string | null }> | null;
}

export interface StackFrameItem {
  filename?: string | null;
  function?: string | null;
  module?: string | null;
  line_number?: number | null;
  column_number?: number | null;
  context_line?: string | null;
  in_app?: boolean;
}

export interface RelevantFileItem {
  path?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  reason?: string | null;
}

export interface CodeSnippetItem {
  path: string;
  start_line?: number;
  end_line?: number;
  symbol_name?: string | null;
  content?: string | null;
}

export interface IncidentDetail extends IncidentListItem {
  error_message: string | null;
  sentry_title: string | null;
  sentry_environment: string | null;
  sentry_release: string | null;
  exception: { type?: string; value?: string } | null;
  stack_trace: StackFrameItem[];
  release: string | null;
  environment: string | null;
  platform: string | null;
  transaction: string | null;
  tags: Record<string, string>;
  timestamp: string | null;
  investigation: InvestigationSummary | null;
  evidence: EvidenceItem[];
  relevant_files: RelevantFileItem[];
  code_snippets: CodeSnippetItem[];
  updated_at: string;
}

export interface GitEvidenceRef {
  commit_sha: string;
  file_path: string;
  start_line?: number | null;
  end_line?: number | null;
  description: string;
}

export interface CandidateCommit {
  commit_sha: string;
  short_sha: string;
  message?: string | null;
  author?: string | null;
  committed_at?: string | null;
  changed_files: string[];
  score: number;
  signals: Record<string, number>;
  relevance_reason?: string;
  context_only?: boolean;
}

export interface RegressionTrace {
  status?: string | null;
  likely_commit?: string | null;
  confidence?: number | null;
  causal_explanation?: string | null;
  evidence_for?: GitEvidenceRef[];
  evidence_against?: GitEvidenceRef[];
  unknowns?: string[];
  recommended_action?: string | null;
  regression_model?: string | null;
  candidates?: CandidateCommit[];
}

export interface TimelineEntry {
  time?: string | null;
  label: string;
  detail?: string | null;
}

export interface DashboardStats {
  total_investigations: number;
  completed_investigations: number;
  failed_investigations: number;
  average_confidence: number;
}

export interface RepositoryItem {
  id: number;
  name: string;
  path: string;
  indexed_files: number;
  created_at: string;
  updated_at: string;
}

export interface HealthStatus {
  status: string;
  version: string;
  components: {
    slack_configured: boolean;
    sentry_configured: boolean;
    ollama_configured: boolean;
    repository_configured: boolean;
  };
}

// --- Fetchers ---------------------------------------------------------------

export const api = {
  stats: () => get<DashboardStats>("/api/stats"),
  incidents: (limit = 50) => get<IncidentListItem[]>(`/api/incidents?limit=${limit}`),
  incident: (id: number | string) => get<IncidentDetail>(`/api/incidents/${id}`),
  evidence: (id: number | string) => get<EvidenceItem[]>(`/api/incidents/${id}/evidence`),
  retrieval: (id: number | string) => get<RetrievalTrace>(`/api/incidents/${id}/retrieval`),
  regression: (id: number | string) => get<RegressionTrace>(`/api/incidents/${id}/regression`),
  timeline: (id: number | string) => get<{ entries: TimelineEntry[] }>(`/api/incidents/${id}/timeline`),
  indexGit: async (): Promise<{ ok: boolean; detail?: string }> => {
    try {
      const response = await fetch(`${API_URL}/api/repositories/index_git`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        return { ok: false, detail: body.detail ?? `HTTP ${response.status}` };
      }
      return { ok: true };
    } catch (error) { return { ok: false, detail: String(error) }; }
  },
  repositories: () => get<RepositoryItem[]>("/api/repositories"),
  health: () => get<HealthStatus>("/health"),

  async triggerIndex(repositoryPath?: string): Promise<{ ok: boolean; detail?: string }> {
    try {
      const response = await fetch(`${API_URL}/api/repositories/index`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(repositoryPath ? { repository_path: repositoryPath } : {}),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        return { ok: false, detail: body.detail ?? `HTTP ${response.status}` };
      }
      return { ok: true };
    } catch (error) {
      return { ok: false, detail: String(error) };
    }
  },
};

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
