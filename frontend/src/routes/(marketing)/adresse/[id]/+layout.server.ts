// Server loader of the identity and the report of the address page (UI-SPEC v3, 04.7-03, LIEU-01):
// `GET /v1/adresse/{ban_id}/rapport`, read once per visit. It stays on the server, like the `/bien`
// loader, so that the API answer is never captured by a universal fetch and injected in the HTML:
// only the public models projected by `getRapportAdresse` (identity, coverage, parcel state, themes
// — no coordinate, no header) cross the SvelteKit serialization. `[id]` is the BAN key served by the search; the page is never indexed (KI-P12,
// T-04-17-02) and never stored by a cache.
// The HTTP status follows the served outcome (SEM-01): 200 for a served or out-of-coverage address
// and for a typed refusal (409, 429, 401, 403 — not an outage, rendered by the page), 404 for an
// unknown key or a street, 503 with `Retry-After` for an outage (5xx or network), 429 once the session has spent its
// weekly allowance of fiches (H-3, `$lib/server/fiche-quota`). A 404 or 503 throws the constant
// title of its contract, rendered by `adresse/+error.svelte` (the boundary above this layout).
// A layout, not the page (FRONT WR-03 of the D-15 review): it reads neither `url` nor its search
// parameters, so a page change of the list (`?page=&g=`, `+page.server.ts`) never reads the
// identity again, hence never calls the live sources of the report again.
import { error } from '@sveltejs/kit';
import { getRapportAdresse, type IssueRapportAdresse } from '$lib/api/adresses';
import { TITRE_ADRESSE_INCONNUE, TITRE_PANNE_ADRESSE, TITRE_VOIE } from '$lib/data/recherche-lieux';
import { fetchApiDeChargeur } from '$lib/server/api-route-fetch';
import { DEFAULT_RETRY_AFTER_SECONDS } from '$lib/utils/etat-page';
import { admettreOuRefuser } from './admission.server';
import type { LayoutServerLoad } from './$types';

export const prerender = false;

export interface AdresseIdentiteData {
	issue: IssueRapportAdresse;
}

export const load: LayoutServerLoad = async ({ params, request, platform, setHeaders, getClientAddress, cookies }): Promise<AdresseIdentiteData> => {
	// Never indexed, never stored by a cache (IN-10 of the D-15 review).
	setHeaders({ 'x-robots-tag': 'noindex, nofollow', 'cache-control': 'private, no-store' });
	// A key the API refuses by its form is unknown, never « Demande refusée » in a 200 (IN-12); past the
	// weekly allowance (H-3), a 429 before the API is read, so a refused fiche costs no API call. The lists
	// pass the same gate (R-16). The place is counted before the read and given back when the key is not a
	// fiche (unknown, a street) or when our side failed; a served page, a typed refusal included, keeps it.
	const rendre = await admettreOuRefuser({ cookies, request, getClientAddress }, params.id);
	// R-07: the first answer does not wait for the slow sources; `suite/+server.ts` brings their values.
	const issue = await getRapportAdresse(fetchApiDeChargeur(request, platform, getClientAddress), params.id, 'courte');
	if (issue.kind === 'inconnue' || issue.kind === 'voie') {
		rendre();
		error(404, issue.kind === 'voie' ? TITRE_VOIE : TITRE_ADRESSE_INCONNUE);
	}
	if (issue.kind === 'panne' && (issue.panne.statut === null || issue.panne.statut >= 500)) {
		rendre();
		// A robot retries instead of indexing an outage (04.7-10, decision 11). The error keeps the headers
		// set by the load; the identity relays no served delay, hence the default minute.
		setHeaders({ 'retry-after': String(DEFAULT_RETRY_AFTER_SECONDS) });
		error(503, TITRE_PANNE_ADRESSE);
	}
	return { issue };
};
