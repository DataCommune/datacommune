import { error } from '@sveltejs/kit';

export const PREPRODUCTION_API_ORIGIN = 'https://dev-api.datacommune.fr';
export const PREPRODUCTION_COOKIE_NAME = 'dev_access_token';
const MAX_REQUEST_BYTES = 1024 * 1024;
const TIMEOUT_MS = 15_000;
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const METHODS = new Set([...SAFE_METHODS, 'POST', 'PUT', 'PATCH', 'DELETE']);
const PATHS = [
	/^\/auth(?:\/|$)/,
	/^\/billing(?:\/|$)/,
	/^\/v1(?:\/|$)/,
	/^\/openapi\.json$/
];
const REQUEST_HEADERS = new Set(['accept', 'accept-language', 'content-type', 'x-api-key']);
const RESPONSE_HEADERS = new Set([
	'cache-control',
	'content-disposition',
	'content-language',
	'content-type',
	'etag',
	'expires',
	'last-modified',
	'retry-after',
	'vary'
]);

export interface AccessSecrets {
	CF_ACCESS_CLIENT_ID?: string;
	CF_ACCESS_CLIENT_SECRET?: string;
}

function validPath(path: string): boolean {
	if (!path.startsWith('/') || path.includes('\\') || /%(?:2e|2f|5c)/i.test(path)) return false;
	try {
		const decoded = decodeURIComponent(path);
		if (decoded.split('/').some((part) => part === '.' || part === '..')) return false;
	} catch {
		return false;
	}
	return PATHS.some((allowed) => allowed.test(path));
}

export function selectAuthCookie(header: string | null): string | null {
	return (
		header
			?.split(';')
			.map((part) => part.trim())
			.find((part) => part.split('=', 1)[0] === PREPRODUCTION_COOKIE_NAME) ?? null
	);
}

function requireSecrets(env: AccessSecrets): [string, string] {
	const id = env.CF_ACCESS_CLIENT_ID?.trim();
	const secret = env.CF_ACCESS_CLIENT_SECRET?.trim();
	if (!id || !secret) throw error(503, 'Accès API préproduction indisponible');
	return [id, secret];
}

export function createPreproductionFetch(
	fetchDirect: typeof fetch,
	env: AccessSecrets,
	cookieHeader: string | null
): typeof fetch {
	const [clientId, clientSecret] = requireSecrets(env);
	const authCookie = selectAuthCookie(cookieHeader);
	return (async (input: RequestInfo | URL, init?: RequestInit) => {
		const source = new URL(input instanceof Request ? input.url : input.toString(), PREPRODUCTION_API_ORIGIN);
		const apiPath = source.pathname.startsWith('/__api/') ? source.pathname.slice('/__api'.length) : source.pathname;
		if (!validPath(apiPath)) throw error(404, 'Route API inconnue');
		const target = new URL(apiPath + source.search, PREPRODUCTION_API_ORIGIN);
		const headers = new Headers();
		const supplied = new Headers(input instanceof Request ? input.headers : undefined);
		new Headers(init?.headers).forEach((value, name) => supplied.set(name, value));
		supplied.forEach((value, name) => {
			if (REQUEST_HEADERS.has(name.toLowerCase())) headers.set(name, value);
		});
		headers.set('CF-Access-Client-Id', clientId);
		headers.set('CF-Access-Client-Secret', clientSecret);
		if (authCookie) headers.set('Cookie', authCookie);
		const directInit = { ...init, headers, redirect: 'manual' as const };
		delete directInit.credentials;
		return fetchDirect(target, directInit);
	}) as typeof fetch;
}

export async function proxyPreproductionRequest(options: {
	request: Request;
	path: string;
	env: AccessSecrets;
	fetchDirect?: typeof fetch;
	preproduction: boolean;
}): Promise<Response> {
	if (!options.preproduction) throw error(404, 'Route inconnue');
	const method = options.request.method.toUpperCase();
	if (!METHODS.has(method) || !validPath('/' + options.path)) throw error(404, 'Route API inconnue');
	const origin = options.request.headers.get('origin');
	if (!SAFE_METHODS.has(method) && origin !== 'https://dev.datacommune.fr') {
		throw error(403, 'Origine refusée');
	}
	const declaredLength = Number(options.request.headers.get('content-length') ?? 0);
	if (!Number.isFinite(declaredLength) || declaredLength > MAX_REQUEST_BYTES) {
		throw error(413, 'Requête trop volumineuse');
	}
	const body = SAFE_METHODS.has(method) ? undefined : await options.request.arrayBuffer();
	if (body && body.byteLength > MAX_REQUEST_BYTES) throw error(413, 'Requête trop volumineuse');
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
	try {
		const apiFetch = createPreproductionFetch(
			options.fetchDirect ?? globalThis.fetch,
			options.env,
			options.request.headers.get('cookie')
		);
		const incomingUrl = new URL(options.request.url);
		const upstream = await apiFetch('/' + options.path + incomingUrl.search, {
			method,
			headers: options.request.headers,
			body,
			signal: controller.signal
		});
		if (upstream.status >= 300 && upstream.status < 400) throw error(502, 'Redirection API refusée');
		const headers = new Headers();
		upstream.headers.forEach((value, name) => {
			if (RESPONSE_HEADERS.has(name.toLowerCase())) headers.set(name, value);
		});
		const setCookies = upstream.headers.getSetCookie?.() ?? [];
		for (const cookie of setCookies) {
			if (!cookie.toLowerCase().startsWith(`${PREPRODUCTION_COOKIE_NAME}=`)) continue;
			const safe = cookie
				.split(';')
				.filter((part) => !/^\s*domain=/i.test(part))
				.join(';');
			headers.append('set-cookie', safe);
		}
		return new Response(upstream.body, { status: upstream.status, headers });
	} catch (cause) {
		if (cause instanceof DOMException && cause.name === 'AbortError') {
			throw error(504, 'Délai API dépassé');
		}
		throw cause;
	} finally {
		clearTimeout(timeout);
	}
}
