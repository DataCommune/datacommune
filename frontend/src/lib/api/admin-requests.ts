import { api } from './client';
import { API_URL } from '$lib/config';

// --- Types (mirror backend Pydantic schemas) ---

export interface AdminRequestEntry {
	id: number;
	ts: string; // ISO datetime
	method: string;
	path: string;
	status_code: number | null;
	duration_ms: number | null;
	client_ip: string | null;
	api_key_id: number | null;
	user_id: number | null;
	user_email: string | null;
}

export interface AdminRequestsList {
	rows: AdminRequestEntry[];
	total: number;
	page: number;
	page_size: number;
}

export interface RequestFilters {
	user_id?: number;
	endpoint?: string;
	status_code?: number;
	date_from?: string; // ISO datetime
	date_to?: string;
	page?: number;
	page_size?: number;
}

function buildQuery(filters: RequestFilters): string {
	const p = new URLSearchParams();
	if (filters.user_id !== undefined) p.set('user_id', String(filters.user_id));
	if (filters.endpoint) p.set('endpoint', filters.endpoint);
	if (filters.status_code !== undefined) p.set('status_code', String(filters.status_code));
	if (filters.date_from) p.set('date_from', filters.date_from);
	if (filters.date_to) p.set('date_to', filters.date_to);
	if (filters.page) p.set('page', String(filters.page));
	if (filters.page_size) p.set('page_size', String(filters.page_size));
	return p.toString();
}

export function listAdminRequests(filters: RequestFilters): Promise<AdminRequestsList> {
	const qs = buildQuery(filters);
	return api<AdminRequestsList>(`/v1/admin/requests${qs ? '?' + qs : ''}`);
}

/** Returns an absolute URL for direct <a download> use.
 *  Credentials (JWT cookie) are sent automatically by the browser on same-origin.
 *  Page and page_size are omitted — full filtered export. */
export function buildExportCsvUrl(filters: RequestFilters): string {
	const exportFilters: RequestFilters = { ...filters, page: undefined, page_size: undefined };
	const qs = buildQuery(exportFilters);
	return `${API_URL}/v1/admin/requests/export.csv${qs ? '?' + qs : ''}`;
}
