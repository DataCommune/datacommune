import { api } from './client';

// --- Types (mirror backend Pydantic schemas from 18-01) ---

export interface AdminSubscriptionRow {
	id: number;
	user_id: number;
	user_email: string | null;
	plan_name: string;
	status: string;
	source: string | null; // 'stripe' | 'manual_grant' | null
	stripe_subscription_id: string;
	current_period_start: string | null; // ISO datetime
	current_period_end: string | null;
	cancel_at_period_end: boolean;
	created_at: string;
	updated_at: string;
}

export interface AdminSubscriptionsList {
	rows: AdminSubscriptionRow[];
	total: number;
	page: number;
	page_size: number;
}

export interface SubscriptionFilters {
	source?: 'stripe' | 'manual_grant';
	plan_name?: string;
	status?: string;
	user_email?: string;
	page?: number;
	page_size?: number;
}

export interface SyncSubscriptionResponse {
	status: string;
	sub_id: number;
	after: {
		plan_name: string;
		status: string;
		source: string;
		quota_monthly: number;
		current_period_end: string | null;
	};
}

// --- API functions ---

export function listAdminSubscriptions(
	filters: SubscriptionFilters
): Promise<AdminSubscriptionsList> {
	const p = new URLSearchParams();
	if (filters.source) p.set('source', filters.source);
	if (filters.plan_name) p.set('plan_name', filters.plan_name);
	if (filters.status) p.set('status', filters.status);
	if (filters.user_email) p.set('user_email', filters.user_email);
	if (filters.page) p.set('page', String(filters.page));
	if (filters.page_size) p.set('page_size', String(filters.page_size));
	const qs = p.toString();
	return api<AdminSubscriptionsList>(
		`/v1/admin/subscriptions${qs ? '?' + qs : ''}`
	);
}

export function syncAdminSubscription(
	subId: number,
	reason: string
): Promise<SyncSubscriptionResponse> {
	return api<SyncSubscriptionResponse>(
		`/v1/admin/subscriptions/${subId}/sync`,
		{
			method: 'POST',
			body: JSON.stringify({ reason })
		}
	);
}
