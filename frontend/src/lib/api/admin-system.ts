import { api } from './client';

export interface DbHealth {
	status: string; // 'up' | 'down'
	connections_active: number | null;
	connections_idle: number | null;
	connections_total: number | null;
	db_size_pretty: string | null;
	db_size_bytes: number | null;
	error: string | null;
}

export interface RateLimitBucket {
	bucket: string;
	count: number;
}

export interface RateLimitSection {
	active_buckets: RateLimitBucket[];
	total_buckets: number;
	window_seconds: number;
}

export interface DiskUsageRow {
	mount: string;
	total_bytes: number | null;
	used_bytes: number | null;
	free_bytes: number | null;
	percent_used: number | null;
	status: string; // 'ok' | 'unavailable'
	detail: string | null;
}

export interface ServiceHealth {
	name: string;
	status: string; // 'up' | 'down' | 'stale' | 'unknown'
	detail: string | null;
}

export interface SystemStatus {
	db: DbHealth;
	rate_limit: RateLimitSection;
	disk: DiskUsageRow[];
	services: ServiceHealth[];
	generated_at: string;
}

export function getAdminSystemStatus(): Promise<SystemStatus> {
	return api<SystemStatus>('/v1/admin/system');
}
