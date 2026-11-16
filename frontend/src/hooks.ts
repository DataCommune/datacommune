import type { Reroute } from '@sveltejs/kit';
import { ACCOUNTS_ENABLED, BILLING_ENABLED } from '$lib/config';

// Top-level segments of the (auth) and (app) route groups. With accounts switched off at build time
// (VITE_ACCOUNTS_ENABLED=false) none of them may answer: the request is rerouted to a path no route
// matches, so SvelteKit renders its regular 404 on the server and on the client alike. A universal
// hook rather than a group layout: the (app) group is client-rendered (ssr = false), so a server
// load of its layout would not run on a direct page request.
const ACCOUNT_SEGMENTS = new Set([
	'login',
	'register',
	'forgot-password',
	'reset-password',
	'verify',
	'settings',
	'billing',
	'mon-espace',
	'admin'
]);

export const ACCOUNTS_DISABLED_PATH = '/__comptes-desactives';

export function rerouteAccounts(
	pathname: string,
	accountsEnabled: boolean,
	billingEnabled: boolean = accountsEnabled
): string | undefined {
	const segment = pathname.split('/')[1] ?? '';
	if (!accountsEnabled) return ACCOUNT_SEGMENTS.has(segment) ? ACCOUNTS_DISABLED_PATH : undefined;
	return !billingEnabled && segment === 'billing' ? ACCOUNTS_DISABLED_PATH : undefined;
}

export const reroute: Reroute = ({ url }) => rerouteAccounts(url.pathname, ACCOUNTS_ENABLED, BILLING_ENABLED);
