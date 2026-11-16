import { api } from './client';

// --- Types (mirror backend Pydantic schemas) ---

export interface AdminUserRow {
	id: number;
	email: string;
	name: string | null;
	is_admin: boolean;
	email_verified: boolean;
	created_at: string;
	last_login: string | null;
	plan: string | null;
	quota_used: number;
	quota_monthly: number;
	api_keys_count: number;
}

export interface AdminUsersListResponse {
	rows: AdminUserRow[];
	total: number;
	page: number;
	page_size: number;
}

export interface AdminApiKeyInfo {
	id: number;
	prefix: string;
	label: string | null;
	quota_used: number;
	quota_monthly: number;
	last_used_at: string | null;
	revoked: boolean;
	is_default: boolean;
	created_at: string;
}

export interface AdminSubscriptionInfo {
	plan_name: string;
	status: string;
	current_period_end: string | null;
	source: string | null;
	stripe_subscription_id: string;
}

export interface AdminRecentActivity {
	id: number;
	action: string;
	reason: string;
	created_at: string;
}

export interface AdminUserDetailResponse extends AdminUserRow {
	api_keys: AdminApiKeyInfo[];
	subscription: AdminSubscriptionInfo | null;
	recent_activity: AdminRecentActivity[];
}

export interface GrantPlanRequest {
	plan: 'free' | 'pro' | 'business' | 'enterprise';
	reason: string;
	end_date: string | null;
	force_downgrade?: boolean;
}

export interface ToggleAdminRequest {
	is_admin: boolean;
	reason: string;
}

export interface ResetQuotaRequest {
	reason: string;
}

export interface RevokeKeysRequest {
	reason: string;
}

export interface ResendVerifRequest {
	reason: string;
}

export interface BulkActionRequest {
	action: 'reset_quota' | 'revoke_keys' | 'resend_verification';
	user_ids: number[];
	reason: string;
}

export interface BulkActionResult {
	user_id: number;
	status: 'ok' | 'error';
	detail?: string | null;
}

export interface BulkActionResponse {
	results: BulkActionResult[];
	total: number;
	failed: number;
}

// --- API functions ---

export interface AdminUsersParams {
	search?: string;
	plan?: string;
	is_admin?: boolean | string;
	email_verified?: boolean | string;
	page?: number;
	page_size?: number;
	sort?: string;
}

export function listAdminUsers(params: AdminUsersParams): Promise<AdminUsersListResponse> {
	const sp = new URLSearchParams();
	if (params.search) sp.set('search', params.search);
	if (params.plan) sp.set('plan', params.plan);
	if (params.is_admin !== undefined && params.is_admin !== '')
		sp.set('is_admin', String(params.is_admin));
	if (params.email_verified !== undefined && params.email_verified !== '')
		sp.set('email_verified', String(params.email_verified));
	if (params.page) sp.set('page', String(params.page));
	if (params.page_size) sp.set('page_size', String(params.page_size));
	if (params.sort) sp.set('sort', params.sort);
	const qs = sp.toString();
	return api<AdminUsersListResponse>(`/v1/admin/users${qs ? '?' + qs : ''}`);
}

export function getAdminUserDetail(userId: number): Promise<AdminUserDetailResponse> {
	return api<AdminUserDetailResponse>(`/v1/admin/users/${userId}`);
}

export function grantPlan(
	userId: number,
	body: GrantPlanRequest
): Promise<{ status: string; user_id: number; plan: string; after: Record<string, unknown> }> {
	return api(`/v1/admin/users/${userId}/grant-plan`, {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export function toggleAdmin(
	userId: number,
	body: ToggleAdminRequest
): Promise<{ status: string; user_id: number; is_admin: boolean; after: Record<string, unknown> }> {
	return api(`/v1/admin/users/${userId}/admin`, {
		method: 'PATCH',
		body: JSON.stringify(body)
	});
}

export function resetQuota(
	userId: number,
	body: ResetQuotaRequest
): Promise<{ status: string; user_id: number; after: Record<string, unknown> }> {
	return api(`/v1/admin/users/${userId}/reset-quota`, {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export function revokeAllKeys(
	userId: number,
	body: RevokeKeysRequest
): Promise<{ status: string; user_id: number; after: Record<string, unknown> }> {
	return api(`/v1/admin/users/${userId}/revoke-keys`, {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export function resendVerification(
	userId: number,
	body: ResendVerifRequest
): Promise<{ status: string; user_id: number; after: Record<string, unknown> }> {
	return api(`/v1/admin/users/${userId}/resend-verification`, {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export function bulkAction(body: BulkActionRequest): Promise<BulkActionResponse> {
	return api(`/v1/admin/users/bulk`, {
		method: 'POST',
		body: JSON.stringify(body)
	});
}
