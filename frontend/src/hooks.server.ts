import type { Handle, HandleFetch, HandleServerError } from '@sveltejs/kit';
import { dev } from '$app/environment';
import { env } from '$env/dynamic/private';
import { API_URL } from '$lib/config';
import { ipVisiteur, versApiInterne } from '$lib/server/api-route-fetch';
import { redirectionPermanente } from '$lib/server/redirections';
import {
	DEFAULT_RETRY_AFTER_SECONDS,
	MAX_RETRY_AFTER_SECONDS,
	OUTAGE_RETRY_HEADER,
	PAGE_STATE_HEADER,
	PAGE_STATE_OUTAGE
} from '$lib/utils/etat-page';
import { SECURITY_HEADERS } from '$lib/utils/security-headers.js';

const NOINDEX_PATH = /^\/bien(?:\/|$)/;
export const NOINDEX_VALUE = 'noindex, nofollow';

/** `Retry-After` of an outage page: the delay the failed API call served, else a default minute. */
function retryAfterSeconds(served: string | null): number {
	if (served === null || !/^\d+$/.test(served) || Number(served) < 1) return DEFAULT_RETRY_AFTER_SECONDS;
	return Math.min(Number(served), MAX_RETRY_AFTER_SECONDS);
}

// A page whose data source failed is rendered whole, then answered 503 with `Retry-After` (04.7-10,
// decision 11): a robot retries instead of indexing an « unavailable » page served as a 200. The
// load posts the internal header (`markOutage`, `$lib/utils/etat-page`); the header is always
// removed, whatever its value, so it never reaches a client. Only a successful answer is changed: a
// redirect or an error keeps its own status. Exported so that the department page, which posts the
// same marker, is covered by this one mechanism.
export function outageResponse(response: Response): Response {
	const state = response.headers.get(PAGE_STATE_HEADER);
	const served = response.headers.get(OUTAGE_RETRY_HEADER);
	if (state === null && served === null) return response;
	if (state !== PAGE_STATE_OUTAGE || !response.ok) {
		response.headers.delete(PAGE_STATE_HEADER);
		response.headers.delete(OUTAGE_RETRY_HEADER);
		return response;
	}
	const headers = new Headers(response.headers);
	headers.delete(PAGE_STATE_HEADER);
	headers.delete(OUTAGE_RETRY_HEADER);
	headers.set('retry-after', String(retryAfterSeconds(served)));
	return new Response(response.body, { status: 503, statusText: 'Service Unavailable', headers });
}

// The response headers a universal load may read while the page is rendered on the server. SvelteKit
// throws on any other (« Failed to get response header ») and serialises the listed ones into the
// page for hydration. `api()` reads the `Retry-After` of a refusal (client.ts): left out, a 429 or a
// 503 of the API stopped being an `ApiError` in the SSR of the pages and its served delay was lost.
export const lisibleParLesChargeurs = (name: string): boolean => name === 'retry-after';

// Applies the canonical list to every response SvelteKit renders. Static assets
// and prerendered pages do NOT pass through here: `@sveltejs/adapter-node`
// serves them from `build/client` and `build/prerendered` with sirv, ahead of
// the SvelteKit app. `server.js` sets the same list on those (KI-P3).
//
// A removed route or a lower-case Corsican code is answered here with a 301 (lib/server/redirections.ts),
// before any route is resolved, and still receives the headers below. Here rather than in Caddy: it
// holds on every instance (work base, public container, local build) and a removed route reaches SvelteKit anyway.
export const securityHeaders: Handle = async ({ event, resolve }) => {
	const cible = event.url ? redirectionPermanente(event.url.pathname) : null;
	const response = cible
		? new Response(null, { status: 301, headers: { location: cible } })
		: outageResponse(await resolve(event, { filterSerializedResponseHeaders: lisibleParLesChargeurs }));
	// KI-P12: DVF sales may not be indexed by search engines. `/bien/*` only redirects (308/307) to
	// `/adresse/*` - the pages that show sales - and a header set by a load is not relied on for a redirect
	// response, so it is set here for every answer under /bien. robots.txt must NOT disallow /bien/:
	// a crawler that cannot fetch the URL never reads this header.
	if (event.url && NOINDEX_PATH.test(event.url.pathname)) response.headers.set('x-robots-tag', NOINDEX_VALUE);
	for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
		if (!response.headers.has(name)) response.headers.set(name, value);
	}
	return response;
};

// The header pass is the whole server hook: no third-party error tracker wraps
// it any more. Keep this a single handle rather than a one-element `sequence` —
// the test asserts identity with `securityHeaders`, which is what proves nothing
// silently re-wraps the request path.
export const handle: Handle = securityHeaders;

// Server-side errors go to the process log - the only place they were ever read
// from anyway.
export const handleError: HandleServerError = ({ error, event }) => {
	console.error('Server-side error on', event.url.pathname, error);
};

const RELAYED_PATHS = /^\/(?:v1|auth|billing)(?:\/|$)/;

// Dev only (owner acceptance on real data, plan 03-13). With VITE_API_URL set to the dev server's
// own origin, the browser reaches the API through the Vite relay (`DEV_API_PROXY_TARGET`,
// vite.config.ts), but SvelteKit resolves a server-side fetch of its own origin internally, where
// no /v1 route exists. This sends those server-side requests to the same target as the relay.
// Without a target, or for any other origin or path, the request is untouched.
export function relayDevApi(target: string | undefined): HandleFetch {
	return async ({ event, request, fetch }) => {
		const url = new URL(request.url);
		if (!target || url.origin !== event.url.origin || !RELAYED_PATHS.test(url.pathname)) {
			return fetch(request);
		}
		const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer();
		return fetch(
			new Request(new URL(url.pathname + url.search, target), {
				method: request.method,
				headers: request.headers,
				body,
				redirect: 'manual'
			})
		);
	};
}

// Production: a server-side fetch of the public API URL (universal loads run on the server) goes to
// the API over the internal network with the visitor's address (see `versApiInterne`). No-op when
// API_INTERNAL_URL is unset, which leaves the dev relay above in charge.
export function internalApiFetch(internalUrl: string | undefined): HandleFetch {
	return ({ event, request, fetch }) =>
		versApiInterne(fetch, {
			apiUrl: API_URL,
			internalUrl,
			ip: ipVisiteur(event.request, event.getClientAddress)
		})(request);
}

const devRelay = relayDevApi(dev ? process.env.DEV_API_PROXY_TARGET : undefined);

export const handleFetch: HandleFetch = (input) =>
	env.API_INTERNAL_URL ? internalApiFetch(env.API_INTERNAL_URL)(input) : devRelay(input);
