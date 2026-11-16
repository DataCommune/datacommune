// Server loader of the lists of the address page: the declared documents (04-18, D-109) and the
// sales of the parcel of the point (04-05, D-115). The identity is read by `+layout.server.ts`:
// this load alone depends on the URL, so a page change of a list reads the lists only, never the
// identity nor Géorisques (FRONT WR-03 of the D-15 review); « Recharger les documents DPE » and
// « Recharger les ventes » invalidate `DEPENDANCE_DOCUMENTS` / `DEPENDANCE_VENTES` only.
// `?page=` and `g` (documents), `ventes_page` and `vg` (sales) are relayed when well formed (the
// API validates them again, T-04-18-01); a page or a token the API would refuse (422: empty, not a
// number, out of 1..100000, a truncated token), a page beyond the last one or a stale generation
// (404 `page_hors_limites`, 409 `generation_changee`) is a 303 back to the first page of that list,
// the parameters of the other lists kept (FRONT WR-02 of the D-15 review, R-19 of 2026-11-10). A
// failure of a list is a state of its section, never an outage of the page; the page renders the
// lists for a served identity only.
// Similar sales (D-114): read only when the URL carries a type (no type is ever assumed, Q6); a
// criterion outside the closed lists is never relayed nor widened (`criteres_non_reconnus`, no
// read); `vr_page` and `vg` follow the rule of the sales list.
// Sales declared at the address (C37): a list of its own (`vd_page`), dated by the same DVF
// generation `vg` once the visitor pages it (its first page without `vd_page` carries no token, so a
// page change of the parcel list never invalidates it); read with the other lists, its failure is
// the outage of its section only.
import { redirect } from '@sveltejs/kit';
import {
	DEPENDANCE_DOCUMENTS,
	DEPENDANCE_VENTES,
	getDocumentsDpe,
	getVentesAdresse,
	getVentesParcelle,
	getVentesRessemblantes,
	parametresDePageValides,
	type IssueComparables,
	type IssueDocumentsDpe,
	type IssueVentes,
	type IssueVentesAdresse
} from '$lib/api/adresses';
import { lireCriteres, pageComparablesValide } from '$lib/data/adresse-ventes';
import { fetchApiDeChargeur } from '$lib/server/api-route-fetch';
import { admettreOuRefuser } from './admission.server';
import type { PageServerLoad } from './$types';

export const prerender = false;

export interface AdresseDocumentsData {
	/** The declared documents of the address, rendered for a served identity only. */
	documents: IssueDocumentsDpe;
	/** The sales of the parcel of the point (04-05), rendered for a served identity only. */
	ventes: IssueVentes;
	/** The sales declared at the address (C37), a relation distinct from the parcel's. */
	ventesAdresse: IssueVentesAdresse;
	/** The similar sales (04-05, D-114): nothing read without a type. */
	comparables: IssueComparables;
}

const PANNE_SANS_DETAIL = { statut: null, titre: null, message: null };

/** First page of the documents, every other parameter of the page kept (the sales, the criteria). */
function premierePageDesDocuments(id: string, lien: URL): string {
	const sp = new URLSearchParams(lien.searchParams);
	sp.delete('g');
	sp.set('page', '1');
	return `/adresse/${encodeURIComponent(id)}?${sp.toString()}`;
}

/** First page of the sales, every other parameter of the page kept (the documents, the criteria). */
function premierePageDesVentes(id: string, lien: URL): string {
	const sp = new URLSearchParams(lien.searchParams);
	sp.delete('vg');
	sp.delete('vr_page');
	sp.delete('vd_page');
	sp.set('ventes_page', '1');
	return `/adresse/${encodeURIComponent(id)}?${sp.toString()}`;
}

export const load: PageServerLoad = async ({ params, url, request, platform, depends, getClientAddress, cookies }): Promise<AdresseDocumentsData> => {
	depends(DEPENDANCE_DOCUMENTS);
	depends(DEPENDANCE_VENTES);
	// A page change of a list requests this node alone: it passes the gate of the identity (R-16).
	await admettreOuRefuser({ cookies, request, getClientAddress }, params.id);
	const lien = url ?? new URL(request.url);
	const generation = lien.searchParams.get('g');
	if (!parametresDePageValides(lien.searchParams.get('page'), generation)) {
		redirect(303, premierePageDesDocuments(params.id, lien));
	}
	const ventesPage = lien.searchParams.get('ventes_page');
	const vg = lien.searchParams.get('vg');
	const vdPage = lien.searchParams.get('vd_page');
	const vgAdresse = vdPage === null ? null : vg;
	if (!parametresDePageValides(ventesPage, vg) || !parametresDePageValides(vdPage, vg) || !pageComparablesValide(lien.searchParams)) {
		redirect(303, premierePageDesVentes(params.id, lien));
	}
	const vrPage = lien.searchParams.get('vr_page');
	const criteres = lireCriteres(lien.searchParams);
	const page = lien.searchParams.get('page') ?? '1';
	const fetchApi = fetchApiDeChargeur(request, platform, getClientAddress);
	const lireComparables = async (): Promise<IssueComparables> => {
		if (!criteres.reconnus) return { kind: 'criteres_non_reconnus' };
		if (!criteres.relayes) return { kind: 'sans_type' };
		// One `vg` for both sales lists: the API dates them by the same DVF generation, a coupling
		// fixed by `test_both_sales_lists_share_one_generation_token` (FRONT IN-05). A list with its
		// own generation would need its own parameter.
		return getVentesRessemblantes(fetchApi, params.id, criteres.relayes, vrPage ?? '1', vg);
	};
	const [documents, ventesLues, adresseLues, comparablesLues] = await Promise.all([
		getDocumentsDpe(fetchApi, params.id, page, generation),
		getVentesParcelle(fetchApi, params.id, ventesPage ?? '1', vg),
		getVentesAdresse(fetchApi, params.id, vdPage ?? '1', vgAdresse),
		lireComparables()
	]);
	if (documents.kind === 'page_invalide' && !(page === '1' && generation === null)) {
		redirect(303, premierePageDesDocuments(params.id, lien));
	}
	if (ventesLues.kind === 'page_invalide' && !((ventesPage ?? '1') === '1' && vg === null)) {
		redirect(303, premierePageDesVentes(params.id, lien));
	}
	if (adresseLues.kind === 'page_invalide' && !((vdPage ?? '1') === '1' && vgAdresse === null)) {
		redirect(303, premierePageDesVentes(params.id, lien));
	}
	if (comparablesLues.kind === 'page_invalide' && !((vrPage ?? '1') === '1' && vg === null)) {
		redirect(303, premierePageDesVentes(params.id, lien));
	}
	// The first page without a token cannot be stale: answering it again would loop, it is an outage.
	return {
		documents: documents.kind === 'page_invalide' ? { kind: 'panne', panne: PANNE_SANS_DETAIL } : documents,
		ventes: ventesLues.kind === 'page_invalide' ? { kind: 'panne', panne: PANNE_SANS_DETAIL } : ventesLues,
		ventesAdresse: adresseLues.kind === 'page_invalide' ? { kind: 'panne', panne: PANNE_SANS_DETAIL } : adresseLues,
		comparables: comparablesLues.kind === 'page_invalide' ? { kind: 'panne', panne: PANNE_SANS_DETAIL } : comparablesLues
	};
};
