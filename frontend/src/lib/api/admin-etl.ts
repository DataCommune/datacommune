import { api } from './client';

// --- Types (mirror backend Pydantic from plan 19-01) ---

export interface AdminEtlPipelineRow {
	pipeline: string;
	last_success_at: string | null;
	last_run_status: string | null;
	last_run_started_at: string | null;
	last_run_duration_seconds: number | null;
	last_run_rows_loaded: number | null;
	last_run_error_message: string | null;
}

export interface AdminEtlPipelineList {
	rows: AdminEtlPipelineRow[];
	total: number;
}

export interface EtlRunRow {
	id: number;
	started_at: string;
	completed_at: string | null;
	status: string;
	total_rows_loaded: number | null;
	total_duration_seconds: number | null;
	error_message: string | null;
	stages: Record<string, unknown> | unknown[] | null;
}

export interface EtlRunHistory {
	pipeline: string;
	rows: EtlRunRow[];
	total: number;
	page: number;
	page_size: number;
}

export interface TriggerEtlResponse {
	status: string; // 'enqueued'
	pipeline: string;
	note: string;
}

// --- API functions ---

export function listEtlPipelines(): Promise<AdminEtlPipelineList> {
	return api<AdminEtlPipelineList>('/v1/admin/etl');
}

export function getEtlPipelineHistory(
	pipelineName: string,
	page = 1,
	pageSize = 20
): Promise<EtlRunHistory> {
	const qs = new URLSearchParams({
		page: String(page),
		page_size: String(pageSize)
	}).toString();
	return api<EtlRunHistory>(`/v1/admin/etl/${encodeURIComponent(pipelineName)}?${qs}`);
}

export function triggerEtlPipeline(
	pipelineName: string,
	reason: string
): Promise<TriggerEtlResponse> {
	return api<TriggerEtlResponse>(`/v1/admin/etl/${encodeURIComponent(pipelineName)}/trigger`, {
		method: 'POST',
		body: JSON.stringify({ reason })
	});
}
