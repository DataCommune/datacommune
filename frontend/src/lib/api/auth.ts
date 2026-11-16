import { api } from './client';

export interface UserInfo {
	id: number;
	email: string;
	name?: string;
	company?: string;
	is_verified: boolean;
	is_admin: boolean;
	created_at: string;
	plan: string;
}

interface TokenResponse {
	access_token: string;
	token_type: 'bearer';
}

interface RegisterResponse {
	message: string;
	requires_verification: boolean;
}

export function login(email: string, password: string) {
	return api<TokenResponse>('/auth/login', {
		method: 'POST',
		body: JSON.stringify({ email, password })
	});
}

export function register(email: string, password: string) {
	return api<RegisterResponse>('/auth/register', {
		method: 'POST',
		body: JSON.stringify({ email, password })
	});
}

export function logout() {
	return api<void>('/auth/logout', { method: 'POST' });
}

export function getMe() {
	return api<UserInfo>('/auth/me');
}

export function forgotPassword(email: string) {
	return api<void>('/auth/forgot-password', {
		method: 'POST',
		body: JSON.stringify({ email })
	});
}

export function resetPassword(token: string, password: string) {
	return api<void>('/auth/reset-password', {
		method: 'POST',
		body: JSON.stringify({ token, new_password: password })
	});
}

export function updateProfile(data: { name?: string; company?: string }) {
	return api<UserInfo>('/auth/profile', {
		method: 'PUT',
		body: JSON.stringify(data)
	});
}

export function changePassword(current_password: string, new_password: string) {
	return api<void>('/auth/password', {
		method: 'PUT',
		body: JSON.stringify({ current_password, new_password })
	});
}

// API key management

export interface ApiKeyInfo {
	id: number;
	name: string;
	key_prefix: string | null;
	quota_monthly: number;
	revoked: boolean;
	environment: string;
	created_at: string;
	usage_count: number;
	is_default: boolean;
}

export interface KeysResponse {
	keys: ApiKeyInfo[];
	account_quota: number;
	account_usage: number;
}

export function listKeys() {
	return api<KeysResponse>('/auth/keys');
}

export function createKey(name: string, environment: 'live' | 'test' = 'live') {
	return api<{ id: number; name: string; key: string; key_prefix: string }>('/auth/keys', {
		method: 'POST',
		body: JSON.stringify({ name, environment })
	});
}

export function revokeKey(keyId: number) {
	return api<void>(`/auth/keys/${keyId}`, { method: 'DELETE' });
}
