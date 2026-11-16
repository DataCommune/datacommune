// `/llms.txt` (04.7-14, Origami R5): what the site is, its URL patterns, sources, licences and the
// attribution it asks for, for programs and AI agents. Prerendered: a static text, no request.
import { ACCOUNTS_ENABLED, API_URL, SITE_URL } from '$lib/config';
import { texteLlms } from '$lib/data/ouverture';
import type { RequestHandler } from './$types';

export const prerender = true;

export const GET: RequestHandler = () =>
	new Response(texteLlms(SITE_URL, API_URL, ACCOUNTS_ENABLED), {
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
