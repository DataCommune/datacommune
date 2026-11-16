import { api } from './client';

// --- Types (mirror backend AdminAuditEntry + AdminAuditListResponse) ---

export interface AdminAuditEntry {
	id: number;
	admin_user_id: number;
	admin_email: string | null;
	action: string;
	target_type: string;
	target_id: string;
	before: Record<string, unknown> | null;
	after: Record<string, unknown> | null;
	reason: string;
	ip: string | null;
	user_agent: string | null;
	created_at: string;
}

export interface AdminAuditListResponse {
	rows: AdminAuditEntry[];
	total: number;
	page: number;
	page_size: number;
}

export interface AdminAuditParams {
	admin_user_id?: number | string;
	action?: string;
	target_type?: string;
	target_id?: string;
	date_from?: string;
	date_to?: string;
	page?: number;
	page_size?: number;
}

export function listAdminAudit(params: AdminAuditParams): Promise<AdminAuditListResponse> {
	const sp = new URLSearchParams();
	if (params.admin_user_id !== undefined && params.admin_user_id !== '')
		sp.set('admin_user_id', String(params.admin_user_id));
	if (params.action) sp.set('action', params.action);
	if (params.target_type) sp.set('target_type', params.target_type);
	if (params.target_id) sp.set('target_id', params.target_id);
	if (params.date_from) sp.set('date_from', params.date_from);
	if (params.date_to) sp.set('date_to', params.date_to);
	if (params.page) sp.set('page', String(params.page));
	if (params.page_size) sp.set('page_size', String(params.page_size));
	const qs = sp.toString();
	return api<AdminAuditListResponse>(`/v1/admin/audit${qs ? '?' + qs : ''}`);
}
