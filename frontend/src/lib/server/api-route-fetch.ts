// Route-scoped API fetch policy of the server loaders (`/bien/[token]`, `/adresse/[id]`), extracted
// from the `/bien` loader without any change of behaviour (04-17, T-04-17-04). A server loader that
// keeps private API answers out of the SSR payload calls the API with the platform's direct fetch,
// never SvelteKit's `event.fetch`, and relays the access cookie alone.
import { markApiResponseAuthMode } from '$lib/api/client';
import { env } from '$env/dynamic/private';
import { API_URL, IS_PREPRODUCTION } from '$lib/config';
import { createPreproductionFetch } from '$lib/server/preproduction-api';

/**
 * Upper bound of one API call of a server loader (FRONT WR-03 of the D-15 review). Above the worst
 * healthy answer of the address report, whose upstream readers run in parallel, each bounded at 5 s
 * by the API (`api/backend/tests/test_rapport_amonts_bornes.py`): a slow upstream turns its own theme
 * into an outage, a stuck API turns the page into its typed outage instead of holding the request.
 */
export const DELAI_API_SSR_MS = 10_000;

/** *fetchImpl* with a deadline, unless the caller passes its own signal. */
export function avecDelaiMaximal(fetchImpl: typeof fetch, delaiMs: number = DELAI_API_SSR_MS): typeof fetch {
	return ((input: RequestInfo | URL, init?: RequestInit) =>
		fetchImpl(input, { ...init, signal: init?.signal ?? AbortSignal.timeout(delaiMs) })) as typeof fetch;
}

/**
 * The API fetch of the loaders of `/adresse/[id]` and `/dpe/[numero]`: the preproduction proxy or
 * the route policy below, bounded by `DELAI_API_SSR_MS`.
 */
export function fetchApiDeChargeur(
	request: Request,
	platform: App.Platform | undefined,
	getClientAddress?: () => string
): typeof fetch {
	return avecDelaiMaximal(
		versApiInterne(
			IS_PREPRODUCTION
				? createPreproductionFetch(globalThis.fetch, platform?.env ?? {}, request.headers.get('cookie'))
				: creerFetchApiRoute(globalThis.fetch, request.headers.get('cookie')),
			{ apiUrl: API_URL, internalUrl: env.API_INTERNAL_URL, ip: ipVisiteur(request, getClientAddress) }
		)
	);
}

/**
 * The visitor's address: the socket address of this server (in production adapter-node reads it from
 * `ADDRESS_HEADER=CF-Connecting-IP`, set by the deployment), `cf-connecting-ip` as a fallback. Null
 * when neither is readable.
 */
export function ipVisiteur(request: Request, getClientAddress?: () => string): string | null {
	try {
		const socket = getClientAddress?.();
		if (socket) return socket;
	} catch {
		// fall through to the header
	}
	return request.headers.get('cf-connecting-ip')?.trim() || null;
}

/**
 * Server-side calls to the public API URL go through Cloudflare, which overwrites `CF-Connecting-IP`
 * with this server's own address: the API would count the frontend as a single visitor. When
 * `internalUrl` (API_INTERNAL_URL, the compose network address of the API) is set, a call to
 * `apiUrl` is rewritten to it and carries the visitor's address as `CF-Connecting-IP` and
 * `X-Forwarded-For` (overwritten, never appended). The internal network is reachable by the proxy
 * and this server only, so the header cannot be forged from outside. Unset `internalUrl`
 * (development, tests): the call is untouched. Other URLs are never touched.
 */
export function versApiInterne(
	fetchImpl: typeof fetch,
	opts: { apiUrl: string; internalUrl: string | undefined; ip: string | null }
): typeof fetch {
	const interne = opts.internalUrl?.trim().replace(/\/+$/, '');
	const api = opts.apiUrl.replace(/\/+$/, '');
	if (!interne) return fetchImpl;
	return ((input: RequestInfo | URL, init?: RequestInit) => {
		const url = input instanceof Request ? input.url : input.toString();
		if (!(url === api || url.startsWith(api + '/') || url.startsWith(api + '?'))) return fetchImpl(input, init);
		const headers = new Headers(input instanceof Request ? input.headers : undefined);
		new Headers(init?.headers).forEach((value, name) => headers.set(name, value));
		if (opts.ip) {
			headers.set('cf-connecting-ip', opts.ip);
			headers.set('x-forwarded-for', opts.ip);
		}
		return fetchImpl(interne + url.slice(api.length), { ...init, headers });
	}) as typeof fetch;
}

/**
 * Extract only the authentication cookie without decoding or exposing its value.
 * Cookie names are case-sensitive and `access_token=` is enough to attempt JWT auth: an empty or
 * expired value will receive the same single 401 fallback as any other unusable cookie. Unrelated
 * browser cookies must never be forwarded to the API.
 */
export function extraireCookieAcces(cookieHeader: string | null): string | null {
	if (cookieHeader == null) return null;
	return (
		cookieHeader
			.split(';')
			.map((cookie) => cookie.trim())
			.find((cookie) => cookie.split('=', 1)[0] === 'access_token') ?? null
	);
}

/**
 * Route-scoped API fetch policy for this server loader.
 *
 * `api()` still prepares the anonymous public-key headers. These calls deliberately use the
 * platform's direct fetch rather than SvelteKit's event.fetch: the latter forwards cookies from
 * the incoming page request to matching external subrequests. RequestInit.credentials cannot be
 * used here because workerd rejects that property during SSR.
 *
 * When the incoming page request carries the JWT cookie, this wrapper replaces the public identity
 * with that cookie alone. The first 401 invalidates JWT mode for the rest of this load: that request
 * is retried once with the public key, then every later call goes directly through public mode.
 * Other statuses are returned unchanged and can never trigger a retry.
 */
export function creerFetchApiRoute(fetchDirect: typeof fetch, cookieHeader: string | null): typeof fetch {
	const cookieAcces = extraireCookieAcces(cookieHeader);
	if (cookieAcces == null) return fetchDirect;
	let jwtInvalide = false;

	return (async (input: RequestInfo | URL, init?: RequestInit) => {
		const publicHeaders = new Headers(input instanceof Request ? input.headers : undefined);
		new Headers(init?.headers).forEach((value, name) => publicHeaders.set(name, value));
		publicHeaders.delete('Cookie');

		// workerd rejects RequestInit.credentials. Direct platform fetch has no incoming-request
		// cookie context, so removing the property and the Cookie header is sufficient isolation.
		const directInit: RequestInit = { ...init, headers: publicHeaders };
		delete directInit.credentials;

		if (jwtInvalide) {
			return markApiResponseAuthMode(await fetchDirect(input, directInit), 'public');
		}

		const authenticatedHeaders = new Headers(publicHeaders);
		authenticatedHeaders.delete('X-API-Key');
		authenticatedHeaders.set('Cookie', cookieAcces);

		const response = await fetchDirect(input, { ...directInit, headers: authenticatedHeaders });
		if (response.status !== 401) return markApiResponseAuthMode(response, 'authenticated');

		jwtInvalide = true;
		const publicResponse = await fetchDirect(input, directInit);
		return markApiResponseAuthMode(publicResponse, 'public');
	}) as typeof fetch;
}
