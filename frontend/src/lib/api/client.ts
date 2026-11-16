import { browser } from '$app/environment';
import { ACCOUNTS_ENABLED, API_URL, getPublicApiHeaders } from '$lib/config';
import { auth } from '$lib/stores/auth.svelte';

const API_BASE = import.meta.env.VITE_API_URL || API_URL;

// D-11 UX: dedicated French message when the shared public (anonymous) API
// key trips the per-IP rate limit. Distinguishes "everyone shares this cap"
// from a personal quota exhaustion, and points visitors to get their own key.
// Without an account space there is no key to create: the message only asks to retry.
const ANONYMOUS_RATE_LIMIT_MESSAGE = ACCOUNTS_ENABLED
	? 'La clé publique partagée a atteint sa limite de requêtes. Réessayez dans quelques instants ou créez votre propre clé API.'
	: 'La limite de requêtes a été atteinte. Réessayez dans quelques instants.';

export type ApiResponseAuthMode = 'public' | 'authenticated';

// A route-scoped fetch adapter can change the credentials prepared by api() (for example, replace
// the public key with an SSR cookie). Keep that effective mode attached to the exact Response
// object: a WeakMap is request-safe, concurrency-safe and never changes the network payload.
const responseAuthModes = new WeakMap<Response, ApiResponseAuthMode>();

export function markApiResponseAuthMode<T extends Response>(
	response: T,
	mode: ApiResponseAuthMode
): T {
	responseAuthModes.set(response, mode);
	return response;
}

/** What a screen needs to word a refusal without reading the server text (panne.ts, 03-17 G1). */
export interface ApiErrorContexte {
	/** Delay served by `Retry-After`, in seconds; null when absent or not a number of seconds. */
	retryAfter?: number | null;
	/** The call went out with the shared public key (anonymous visitor), not an account. */
	anonyme?: boolean;
}

export class ApiError extends Error {
	public retryAfter: number | null;
	public anonyme: boolean;

	constructor(
		public status: number,
		public detail: string,
		public isAnonymous429: boolean = false,
		public problemTitle: string | null = null,
		contexte: ApiErrorContexte = {}
	) {
		super(detail);
		this.name = 'ApiError';
		this.retryAfter = contexte.retryAfter ?? null;
		this.anonyme = contexte.anonyme ?? false;
	}
}

/** `Retry-After` as delay-seconds, the only form the API sends (app/rate_limit.py). */
export function secondesRetryAfter(res: Response): number | null {
	const valeur = res.headers?.get?.('Retry-After')?.trim();
	return valeur && /^\d+$/.test(valeur) ? Number(valeur) : null;
}

export async function api<T>(
	path: string,
	options: RequestInit = {},
	// SSR: universal `load` functions pass SvelteKit's instrumented fetch so the
	// request works on the Worker as well as in the browser. Defaults to global fetch.
	fetchImpl: typeof fetch = fetch
): Promise<T> {
	const headers: Record<string, string> = {
		'Content-Type': 'application/json',
		...(options.headers as Record<string, string>)
	};

	// Logged-in users rely on JWT cookie (server resolves their default key).
	// Anonymous visitors use the shared public API key when configured.
	const isAnonymous = !auth.isAuthenticated;
	if (isAnonymous) {
		Object.assign(headers, getPublicApiHeaders());
	}

	// workerd (Cloudflare Workers SSR) does not implement RequestInit.credentials and throws on it.
	// Browser calls use credentials=include; a server route may instead pass a scoped fetch adapter
	// that replaces the prepared public key with its incoming JWT cookie.
	const res = await fetchImpl(`${API_BASE}${path}`, {
		...(browser ? { credentials: 'include' as const } : {}),
		headers,
		...options
	});
	const markedAuthMode = responseAuthModes.get(res);
	responseAuthModes.delete(res);
	const responseIsAnonymous =
		markedAuthMode === undefined ? isAnonymous : markedAuthMode === 'public';

	if (!res.ok) {
		const body = (await res.json().catch(() => ({}))) as Record<string, string>;
		// Only app/rate_limit.py's per-IP ceiling sets Retry-After; quota/account
		// 429s (ErreurApiError, e.g. "Quota mensuel epuise") do not. This tells
		// apart "everyone sharing this IP is capped" from other 429 causes so we
		// don't mislabel a real quota message as the shared-key rate limit.
		const isAnonymous429 =
			responseIsAnonymous && res.status === 429 && Boolean(res.headers?.has('Retry-After'));
		const detail = isAnonymous429
			? ANONYMOUS_RATE_LIMIT_MESSAGE
			: (body.detail ?? body.erreur ?? body.title ?? res.statusText);
		throw new ApiError(res.status, detail, isAnonymous429, body.title ?? null, {
			retryAfter: secondesRetryAfter(res),
			anonyme: responseIsAnonymous
		});
	}

	if (res.status === 204) return undefined as T;
	return res.json();
}
