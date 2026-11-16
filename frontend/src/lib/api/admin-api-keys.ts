import { api } from './client';

// --- Types (mirror backend Pydantic schemas) ---

export interface AdminApiKeyRow {
	id: number;
	prefix: string | null;
	label: string | null;
	user_id: number | null;
	owner_email: string | null;
	quota_used: number;
	quota_monthly: number;
	revoked: boolean;
	is_default: boolean;
	created_at: string;
	last_used_at: string | null;
}

export interface AdminApiKeysList {
	rows: AdminApiKeyRow[];
	total: number;
	page: number;
	page_size: number;
}

export interface ApiKeyFilters {
	owner_email?: string;
	revoked?: boolean;
	page?: number;
	page_size?: number;
}

// --- API functions ---

export function listAdminApiKeys(filters: ApiKeyFilters): Promise<AdminApiKeysList> {
	const p = new URLSearchParams();
	if (filters.owner_email) p.set('owner_email', filters.owner_email);
	if (filters.revoked !== undefined) p.set('revoked', String(filters.revoked));
	if (filters.page) p.set('page', String(filters.page));
	if (filters.page_size) p.set('page_size', String(filters.page_size));
	const qs = p.toString();
	return api<AdminApiKeysList>(`/v1/admin/api-keys${qs ? '?' + qs : ''}`);
}

export function revokeAdminApiKey(
	keyId: number,
	reason: string
): Promise<{ status: string; key_id: number; after: { revoked: boolean } }> {
	return api(`/v1/admin/api-keys/${keyId}/revoke`, {
		method: 'POST',
		body: JSON.stringify({ reason })
	});
}

export function updateAdminApiKeyQuota(
	keyId: number,
	quotaMonthly: number,
	reason: string
): Promise<{ status: string; key_id: number; after: { quota_monthly: number } }> {
	return api(`/v1/admin/api-keys/${keyId}/quota`, {
		method: 'PATCH',
		body: JSON.stringify({ quota_monthly: quotaMonthly, reason })
	});
}
