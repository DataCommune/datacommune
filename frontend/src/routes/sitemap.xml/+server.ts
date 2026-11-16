// `/sitemap.xml` (04.7-10, decision 5): the index of the sitemaps, served at the request from the
// departements the API says are indexable. `robots.txt` points here. Never prerendered and never a
// static file: sirv would serve one before SvelteKit (a build check refuses any `sitemap*.xml`).
import { SITE_URL } from '$lib/config';
import { documentIndex, reponseSitemap } from '$lib/server/sitemap-source';
import type { RequestHandler } from './$types';

export const prerender = false;

export const GET: RequestHandler = ({ fetch }) => reponseSitemap(documentIndex({ site: SITE_URL, fetch }));
