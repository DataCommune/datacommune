// Former detail page of a dataset (H4, D-40): a permanent redirect to its source in « Données &
// méthode » — the detail opened (`?jeu=<key>`, phase 04.5 plan 11) and its row targeted. Never
// prerendered, so the answer is a real HTTP 308. The target is read in the static table of historical
// slugs; an unknown slug is a 404 and is never reflected (T-03-11-01).
import { error, redirect } from '@sveltejs/kit';
import { cleDuSlug } from '$lib/data/dataset-slugs';
import type { PageLoad } from './$types';

export const prerender = false;

export const load: PageLoad = ({ params }) => {
	const cle = cleDuSlug(params.slug);
	if (cle === null) error(404, 'Jeu de données introuvable');
	redirect(308, `/datasets?jeu=${encodeURIComponent(cle)}#${encodeURIComponent(cle)}`);
};
