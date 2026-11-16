// Follow-up of the first view of an address fiche (R-07, 2026-11-10). The fiche is served after a
// short wait of the live sources, the criteria still being read said « en cours »; the page asks this
// endpoint once, and it answers when every read has ended (the API joins the reads the first view
// started: no producer is called twice). Same public projection as the loader, never the API answer.
// It passes the gate of the fiche (`admettreOuRefuser`): the allowance counts distinct fiches, so the
// follow-up of a fiche already counted costs nothing, and this endpoint opens no way around the gate.
// Never indexed, never stored: a report is private to its visitor's allowance.
import { error, json } from '@sveltejs/kit';
import { getRapportAdresse } from '$lib/api/adresses';
import { TITRE_PANNE_ADRESSE } from '$lib/data/recherche-lieux';
import { fetchApiDeChargeur } from '$lib/server/api-route-fetch';
import { admettreOuRefuser } from '../admission.server';
import type { RequestHandler } from './$types';

export const prerender = false;

/**
 * Bound of the complete read: above the end of the wait of the API (12 s, `ECHEANCE_SUITE_S`) and
 * the database reads that follow it, so that the API decides what an outage is, not this deadline.
 */
const DELAI_SUITE_MS = 15_000;

export const GET: RequestHandler = async ({ params, request, platform, setHeaders, getClientAddress, cookies }) => {
	setHeaders({ 'x-robots-tag': 'noindex, nofollow', 'cache-control': 'private, no-store' });
	const rendre = await admettreOuRefuser({ cookies, request, getClientAddress }, params.id);
	const issue = await getRapportAdresse(fetchApiDeChargeur(request, platform, getClientAddress), params.id, 'complete', {
		signal: AbortSignal.timeout(DELAI_SUITE_MS)
	});
	if (issue.kind !== 'ok') {
		// Not a report: the page keeps what it has and says so; a place counted here is given back.
		rendre();
		error(503, TITRE_PANNE_ADRESSE);
	}
	return json({ issue });
};
