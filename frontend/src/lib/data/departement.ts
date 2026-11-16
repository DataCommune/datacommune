// Reading of the departement pages (04.7-10, decisions 6 and 7), shared by the universal loads of
// `/departement/[code]` and `/departements`. Three honest outcomes, never confused with one another:
//   - the page is served: the key figures of the departement (each block with its own state), the
//     list of departements and the pages of the departement (its A-Z directory), whose links
//     complete the page; either list failing alone leaves the figures and drops its links, it is
//     not an outage of the page;
//   - a 404: the code is malformed (before any request) or the referential does not know it;
//   - an outage: the figures did not answer (the whole route is a 503, nothing partial is shown).
//     The load marks it for `hooks.server.ts`, which answers 503 with `Retry-After`, the page
//     staying rendered (same mechanism as the commune fiche, `$lib/utils/etat-page`).
// The departement list is read from `GET /v1/departements` only: no territory is written here.
import { error } from '@sveltejs/kit';
import { ApiError } from '$lib/api/client';
import type { SourceAttribution } from '$lib/api/commune';
import {
	getDepartementFiches,
	getDepartementSynthese,
	listDepartements,
	type DepartementRow,
	type FicheDepartementRow,
	type DepartementSynthese,
	type ReponseDepartementSynthese
} from '$lib/api/communes';
import { DEPARTEMENTS } from '$lib/data/departements';
import { detailPanne, type DetailPanne } from '$lib/data/panne';
import { markOutage, type SetHeaders } from '$lib/utils/etat-page';

type Fetch = typeof fetch;

/** Departement code shape: two digits, 2A/2B for Corsica, three digits starting with 97 overseas. */
export const CODE_DEPARTEMENT = /^(\d{2}|2[AB]|97\d)$/;

/** Name of a departement from the static list, or the code alone when the list does not know it. */
export function nomDepartement(code: string): string {
	return DEPARTEMENTS.find((d) => d.code === code)?.nom ?? `Département ${code}`;
}

export type DepartementPageData =
	| {
			etat: 'synthese';
			code: string;
			synthese: DepartementSynthese;
			/** Attribution of the sources of the figures (dvf, dpe, demographie), as served. */
			sources: SourceAttribution[];
			/** Every departement, for the cross links; null when that read failed. */
			departements: DepartementRow[] | null;
			/** Every commune and arrondissement page of the departement, for its A-Z directory; null when that read failed. */
			fiches: FicheDepartementRow[] | null;
	  }
	| {
			etat: 'panne';
			code: string;
			nom: string;
			detail: DetailPanne;
			/** Served `Retry-After` of the failure, in seconds; null when none was served. */
			retryAfter: number | null;
	  };

/** The attribution of a response: one source, a list, or none, read as a list. */
function enListe(source: SourceAttribution | SourceAttribution[] | undefined): SourceAttribution[] {
	if (!source) return [];
	return Array.isArray(source) ? source : [source];
}

export async function lireDepartement(
	codeDemande: string,
	fetch: Fetch,
	options: { setHeaders?: SetHeaders } = {}
): Promise<DepartementPageData> {
	const code = codeDemande.toUpperCase();
	if (!CODE_DEPARTEMENT.test(code)) {
		error(404, `Aucun département ne porte le code ${codeDemande}.`);
	}

	const [lues, liste, pages] = await Promise.allSettled([
		getDepartementSynthese(code, fetch),
		listDepartements(fetch),
		getDepartementFiches(code, fetch)
	]);
	// A 200 outside the contract (a proxy page, a version skew during a deploy) is an outage too, not an
	// exception that ends on the root error page without a way to reload (review 2026-11-10, R-20).
	const figures: PromiseSettledResult<ReponseDepartementSynthese> =
		lues.status === 'fulfilled' && (typeof lues.value?.donnees !== 'object' || lues.value.donnees === null)
			? { status: 'rejected', reason: new Error('/v1/departements/{code}: donnees is not an object') }
			: lues;
	if (figures.status === 'rejected') {
		const err = figures.reason;
		if (err instanceof ApiError && (err.status === 404 || err.status === 422)) {
			error(404, `Aucun département ne porte le code ${codeDemande}.`);
		}
		const retryAfter = err instanceof ApiError ? err.retryAfter : null;
		markOutage(options.setHeaders, retryAfter);
		return { etat: 'panne', code, nom: nomDepartement(code), detail: detailPanne(err), retryAfter };
	}

	return {
		etat: 'synthese',
		code,
		synthese: figures.value.donnees,
		sources: enListe(figures.value.meta?.source),
		departements: liste.status === 'fulfilled' ? liste.value : null,
		fiches: pages.status === 'fulfilled' && Array.isArray(pages.value?.fiches) ? pages.value.fiches : null
	};
}

/** The departements of one region (or of none), in the order of their codes. */
export interface GroupeRegion {
	region: string | null;
	departements: DepartementRow[];
}

/** Rank of a code in the official order (01 to 19, 2A, 2B, 21 to 95, overseas); unknown codes last. */
function rang(code: string): number {
	const i = DEPARTEMENTS.findIndex((d) => d.code === code);
	return i < 0 ? DEPARTEMENTS.length : i;
}

/**
 * The departements grouped by region, regions in alphabetical order and the departements of a
 * region in the order of their codes. A departement without region is never put under another one:
 * it closes the list, in a group of its own.
 */
export function groupesParRegion(lignes: readonly DepartementRow[]): GroupeRegion[] {
	const parRegion = new Map<string | null, DepartementRow[]>();
	for (const ligne of lignes) {
		const region = ligne.region ?? null;
		parRegion.set(region, [...(parRegion.get(region) ?? []), ligne]);
	}
	const groupes = [...parRegion.entries()].map(([region, departements]) => ({
		region,
		departements: [...departements].sort((a, b) => rang(a.code) - rang(b.code) || a.code.localeCompare(b.code))
	}));
	return groupes.sort((a, b) => {
		if (a.region === null) return 1;
		if (b.region === null) return -1;
		return a.region.localeCompare(b.region, 'fr');
	});
}

export type DepartementsPageData =
	| { etat: 'liste'; groupes: GroupeRegion[]; total: number }
	| { etat: 'panne'; detail: DetailPanne; retryAfter: number | null };

export async function lireDepartements(
	fetch: Fetch,
	options: { setHeaders?: SetHeaders } = {}
): Promise<DepartementsPageData> {
	try {
		const lignes = await listDepartements(fetch);
		return { etat: 'liste', groupes: groupesParRegion(lignes), total: lignes.length };
	} catch (err) {
		const retryAfter = err instanceof ApiError ? err.retryAfter : null;
		markOutage(options.setHeaders, retryAfter);
		return { etat: 'panne', detail: detailPanne(err), retryAfter };
	}
}
