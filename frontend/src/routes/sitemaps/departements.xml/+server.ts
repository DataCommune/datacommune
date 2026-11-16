// `/sitemaps/departements.xml` (04.7-10, decision 5): the pages of the indexable departements, each
// dated by the day its data last changed (the API's `derniere_importation`).
import { SITE_URL } from '$lib/config';
import { documentDepartements, reponseSitemap } from '$lib/server/sitemap-source';
import type { RequestHandler } from './$types';

export const prerender = false;

export const GET: RequestHandler = ({ fetch }) => reponseSitemap(documentDepartements({ site: SITE_URL, fetch }));
