// Server loader of the DPE document page (UI-SPEC « /dpe/[numero] », D-102, LIEU-03). It stays on
// the server, like the address loader, so that the API answer is never captured by a universal
// fetch and injected in the HTML: only the public model projected by `getDocumentDpe` crosses the
// SvelteKit serialization. `[numero]` is the ADEME number of the document (D-102), a public datum;
// the page is `noindex, follow` (Q2). A number that does not have the ADEME form is never sent to
// the API (SEM-01).
// The HTTP status follows the served outcome, as on `/adresse/[id]` (04-DECISIONS-TEXTES,
// 2026-10-05, 04-04 row 13): 200 for a served document and for a typed refusal (409, 429, 401,
// 403 — not an outage, rendered by the page), 404 for an unknown number (with the served coverage)
// and for a malformed number, 503 for an outage (5xx, refused publication, network). A 404 or 503
// throws the constant message of its state, rendered by `+error.svelte` of this route.
import { error } from '@sveltejs/kit';
import { getDocumentDpe, NUMERO_DPE_RE, type IssueDocumentDpe } from '$lib/api/documents-dpe';
import { ERREUR_DPE_INCONNU, TITRE_NUMERO_NON_VALIDE, TITRE_PANNE_DOCUMENT } from '$lib/data/document-dpe';
import { fetchApiDeChargeur } from '$lib/server/api-route-fetch';
import { DEFAULT_RETRY_AFTER_SECONDS } from '$lib/utils/etat-page';
import type { PageServerLoad } from './$types';

export const prerender = false;

export interface DocumentPageData {
	/** The number of the URL, as served (rendered escaped). */
	numero: string;
	/** A served document or a typed refusal; every other outcome is thrown with its status. */
	issue: Exclude<IssueDocumentDpe, { kind: 'inconnu' }>;
}

export const load: PageServerLoad = async ({ params, request, platform, setHeaders, getClientAddress }): Promise<DocumentPageData> => {
	// Never stored by a cache (IN-10 of the D-15 review).
	setHeaders({ 'x-robots-tag': 'noindex, follow', 'cache-control': 'private, no-store' });
	if (!NUMERO_DPE_RE.test(params.numero)) error(404, TITRE_NUMERO_NON_VALIDE);
	// Bounded in time like the address loaders (FRONT WR-03 of the D-15 review).
	const issue = await getDocumentDpe(fetchApiDeChargeur(request, platform, getClientAddress), params.numero);
	if (issue.kind === 'inconnu') error(404, { message: ERREUR_DPE_INCONNU, departements: issue.departements });
	if (issue.kind === 'panne' && (issue.panne.statut === null || issue.panne.statut >= 500)) {
		// An outage tells a robot when to come back (04.7-10, decision 11; review R-21). The 503 is thrown, so the
		// hook that marks the 200 outages cannot see it: the header is set here. The delay the API served is not
		// carried by `getDocumentDpe`, hence the default minute.
		setHeaders({ 'retry-after': String(DEFAULT_RETRY_AFTER_SECONDS) });
		error(503, TITRE_PANNE_DOCUMENT);
	}
	return { numero: params.numero, issue };
};
