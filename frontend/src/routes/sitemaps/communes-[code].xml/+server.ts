// `/sitemaps/communes-[code].xml` (04.7-10, decision 5): the fiches (communes, and the arrondissements
// of Paris, Lyon and Marseille where they are hosted) of one indexable departement, each dated by the
// day the data of the fiches last changed. A departement that is not indexable (prices not hosted or
// not imported), unknown, or written otherwise than the API writes it (`2A`, not `2a`) is a 404.
import { SITE_URL } from '$lib/config';
import { documentCommunes, reponseSitemap } from '$lib/server/sitemap-source';
import type { RequestHandler } from './$types';

export const prerender = false;

export const GET: RequestHandler = ({ fetch, params }) =>
	reponseSitemap(documentCommunes({ site: SITE_URL, fetch }, params.code));
