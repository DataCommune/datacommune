// `/sitemaps/pages.xml` (04.7-10, decision 5): the static pages that are indexable, with no date (a
// page's date would be a guess). No API call.
import { SITE_URL } from '$lib/config';
import { documentPages, reponseSitemap } from '$lib/server/sitemap-source';
import type { RequestHandler } from './$types';

export const prerender = false;

export const GET: RequestHandler = () => reponseSitemap(Promise.resolve(documentPages(SITE_URL)));
