import { api } from './client';

// --- Types (mirror backend Pydantic schemas) ---

export interface AdminOverview {
	users_count: number;
	active_keys_count: number;
	/** No persisted billing amounts/cadence; never infer revenue from access rights. */
	mrr_eur_estimated: null;
	mrr_status: 'unavailable';
	mrr_reason: 'billing_amounts_not_persisted';
	requests_24h: number;
}

export interface HeatmapDay {
	day: string; // ISO YYYY-MM-DD
	count: number;
}

export interface AdminUserHeatmap {
	days: HeatmapDay[];
	retention_days: number;
}

// --- API functions ---

export function getAdminOverview(): Promise<AdminOverview> {
	return api<AdminOverview>('/v1/admin/overview');
}

export function getAdminUserHeatmap(userId: number): Promise<AdminUserHeatmap> {
	return api<AdminUserHeatmap>(`/v1/admin/users/${userId}/heatmap`);
}
