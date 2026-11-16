// Directory of communes (04.6-02, PROPOSITION § 4 and § 9). Pure module, zero Svelte imports: the
// page reads its filters from the URL here, builds its links here and renders the price cells this
// returns, so every state is tested without a browser.
//
// FILTERS LIVE IN THE URL: `/communes` without `q` is the directory; `nom`, `departement`,
// `population` (a fixed range), `couverture`, `tri` and `page` are plain GET parameters, so a
// filtered page is shareable and works without JavaScript. A value the page does not know is
// dropped rather than sent: the API would answer 422 on a query the visitor cannot see is
// malformed. Defaults are left out of the links.
//
// PRICE CELLS KEEP THE SERVED STATE (SEM-01, D-47): a value with its sales and window; « n.d. »
// under the threshold with the served count; « Aucune vente retenue » only when the served count
// is 0; the glossary label of any other state (« Non renseigné » for not_reported, the reason of an
// out-of-coverage state); « Prix indisponible » when the read failed. A null is never a 0.

import type { AnnuaireParams, PrixTypeAnnuaire } from '$lib/api/communes';
import { libelleCourtArrondissement } from './arrondissements';
import { periodeEnLettres } from './commune-kpi';
import { DEPARTEMENTS } from './departements';
import { libelleEtat } from './etats-libelles';

export const ANNUAIRE_PAGE_SIZE = 50;
const NOM_MAX = 100;

/** Fixed population ranges, inclusive bounds on the served population (H6). */
export const TRANCHES_POPULATION = [
	{ valeur: '0-499', libelle: 'Moins de 500 habitants', min: 0, max: 499 },
	{ valeur: '500-1999', libelle: '500 à 1 999 habitants', min: 500, max: 1999 },
	{ valeur: '2000-9999', libelle: '2 000 à 9 999 habitants', min: 2000, max: 9999 },
	{ valeur: '10000-49999', libelle: '10 000 à 49 999 habitants', min: 10000, max: 49999 },
	{ valeur: '50000-', libelle: '50 000 habitants et plus', min: 50000, max: undefined }
] as const;

export const TRIS = [
	{ valeur: 'population-desc', libelle: 'Population décroissante', sort_by: 'population', order: 'desc' },
	{ valeur: 'population-asc', libelle: 'Population croissante', sort_by: 'population', order: 'asc' },
	{ valeur: 'nom-asc', libelle: 'Nom, de A à Z', sort_by: 'nom', order: 'asc' },
	{ valeur: 'nom-desc', libelle: 'Nom, de Z à A', sort_by: 'nom', order: 'desc' }
] as const;

export type Couverture = 'servies' | 'toutes';
export type Tri = (typeof TRIS)[number]['valeur'];

export interface FiltresAnnuaire {
	nom: string;
	departement: string;
	/** `valeur` of a population range, '' for every population. */
	population: string;
	couverture: Couverture;
	tri: Tri;
	page: number;
}

export const FILTRES_PAR_DEFAUT: FiltresAnnuaire = {
	nom: '',
	departement: '',
	population: '',
	couverture: 'servies',
	tri: 'population-desc',
	page: 1
};

/** The directory filters of a URL; an unknown value falls back to its default. */
export function lireFiltresAnnuaire(params: URLSearchParams): FiltresAnnuaire {
	const departement = (params.get('departement') ?? '').trim().toUpperCase();
	const population = params.get('population') ?? '';
	const tri = params.get('tri') ?? '';
	// `Infinity` or `1e309` would reach the API as a non-integer page: anything not finite is page 1.
	const page = Math.trunc(Number(params.get('page') ?? '1'));
	return {
		nom: (params.get('nom') ?? '').trim().slice(0, NOM_MAX),
		departement: DEPARTEMENTS.some((d) => d.code === departement) ? departement : '',
		population: TRANCHES_POPULATION.some((t) => t.valeur === population) ? population : '',
		couverture: params.get('couverture') === 'toutes' ? 'toutes' : 'servies',
		tri: TRIS.find((t) => t.valeur === tri)?.valeur ?? FILTRES_PAR_DEFAUT.tri,
		page: Number.isFinite(page) && page >= 1 ? page : 1
	};
}

/**
 * The API query of these filters (the page size is fixed). The directory always asks for the
 * arrondissements (04.7-09): the page lists them under their commune (`grouperParCommune`), the
 * commune keeping its own row and its own prices, never the sum of its arrondissements.
 */
export function parametresApi(filtres: FiltresAnnuaire): AnnuaireParams {
	const tranche = TRANCHES_POPULATION.find((t) => t.valeur === filtres.population);
	const tri = TRIS.find((t) => t.valeur === filtres.tri) ?? TRIS[0];
	return {
		nom: filtres.nom || undefined,
		departement: filtres.departement || undefined,
		population_min: tranche?.min,
		population_max: tranche?.max,
		couverture: filtres.couverture,
		sort_by: tri.sort_by,
		order: tri.order,
		page: filtres.page,
		per_page: ANNUAIRE_PAGE_SIZE,
		inclure_arrondissements: true
	};
}

/** The URL parameters of these filters, in a stable order; defaults are left out. */
export function champsAnnuaire(filtres: FiltresAnnuaire, changes: Partial<FiltresAnnuaire> = {}): [string, string][] {
	const suivants = { ...filtres, ...changes };
	const champs: [string, string][] = [];
	for (const cle of ['nom', 'departement', 'population', 'couverture', 'tri', 'page'] as const) {
		const valeur = suivants[cle];
		if (valeur !== FILTRES_PAR_DEFAUT[cle] && valeur !== '') champs.push([cle, String(valeur)]);
	}
	return champs;
}

/** Shareable link of the directory with *changes* applied; defaults are left out. */
export function hrefAnnuaire(filtres: FiltresAnnuaire, changes: Partial<FiltresAnnuaire> = {}): string {
	const qs = new URLSearchParams(champsAnnuaire(filtres, changes)).toString();
	return `/communes${qs ? '?' + qs : ''}`;
}

/** The pages within two of `page`, in order: the numbered links of the pagination (4.9 lot 7, C5). */
export function pagesVoisines(page: number, total: number): number[] {
	const debut = Math.max(1, page - 2);
	const fin = Math.min(total, page + 2);
	return Array.from({ length: Math.max(0, fin - debut + 1) }, (_, i) => debut + i);
}

/** At least one filter narrows the selection (the page number is not a filter). */
export function filtresActifs(filtres: FiltresAnnuaire): boolean {
	return Boolean(filtres.nom || filtres.departement || filtres.population || filtres.couverture !== 'servies');
}

// --- price cells ---------------------------------------------------------------------------

export type CellulePrix =
	| { kind: 'valeur'; texte: string; detail: string | null }
	| { kind: 'nd'; texte: string; detail: string | null }
	| { kind: 'aucune-vente'; texte: string; detail: string | null }
	| { kind: 'etat'; texte: string; detail: null }
	| { kind: 'indisponible'; texte: string; detail: null };

export const PRIX_INDISPONIBLE = 'Prix indisponible';
export const AUCUNE_VENTE_RETENUE = 'Aucune vente retenue';
export const ETAT_NON_DECLARE = 'État non déclaré';

const entier = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

function ventes(n: number): string {
	return `${entier.format(n)} ${n === 1 ? 'vente' : 'ventes'}`;
}

/** The one window of every price of the page, said once above the list (« Prix médians … du 31 décembre 2024 au … »). */
export function periodeDesPrix(fenetre: string | null): string | null {
	return fenetre ? `Prix médians au m² des ventes retenues (DVF) ${periodeEnLettres(fenetre)}` : null;
}

/** The same window for one price read on its own (screen reader, card): « ventes retenues du … ». */
export function periodeDUnPrix(cellule: CellulePrix, fenetre: string | null): string | null {
	if (!fenetre || cellule.kind === 'etat' || cellule.kind === 'indisponible') return null;
	return `ventes retenues ${periodeEnLettres(fenetre)}`;
}

/** What a price cell says, from the served price of one type; the window is the page's own. */
export function celluleDePrix(prix: PrixTypeAnnuaire | null): CellulePrix {
	if (prix === null) return { kind: 'indisponible', texte: PRIX_INDISPONIBLE, detail: null };
	if (prix.mediane_m2 !== null) {
		return { kind: 'valeur', texte: `${entier.format(prix.mediane_m2)} €/m²`, detail: prix.effectif === null ? null : ventes(prix.effectif) };
	}
	const etat = prix.etat;
	if (etat?.etat === 'insufficient_sample' && prix.effectif === 0) {
		return { kind: 'aucune-vente', texte: AUCUNE_VENTE_RETENUE, detail: null };
	}
	if (etat?.etat === 'insufficient_sample') {
		const texte = prix.effectif === null ? 'n.d.' : `n.d. (${ventes(prix.effectif)})`;
		return { kind: 'nd', texte, detail: null };
	}
	// An absence the API did not declare is not disguised as a state (D-47): it is said as such.
	const texte = etat ? libelleEtat(etat.etat, etat.motif) || ETAT_NON_DECLARE : ETAT_NON_DECLARE;
	return { kind: 'etat', texte, detail: null };
}

// --- population ----------------------------------------------------------------------------

/**
 * « 54 699 habitants · 2022 · INSEE » when the served value is dated (H6), else the unit alone. `suite` is the
 * whole text after the number; `unite` and `millesime` (« 2022 · INSEE », null when undated) let a page set
 * the year and the source on a line of their own, never a « · » left at the end of a line (T8, 2026-11-10).
 */
export function populationServie(commune: {
	population: number | null;
	population_annee: number | null;
	population_source: 'demographie' | 'geo_communes' | null;
}): { nombre: string; suite: string; unite: string; millesime: string | null } | null {
	if (commune.population === null) return null;
	const unite = commune.population === 1 ? 'habitant' : 'habitants';
	const millesime =
		commune.population_source === 'demographie' && commune.population_annee !== null ? `${commune.population_annee} · INSEE` : null;
	return { nombre: entier.format(commune.population), suite: millesime ? `${unite} · ${millesime}` : unite, unite, millesime };
}

// --- arrondissements ----------------------------------------------------------------------

/**
 * Name of an arrondissement listed under its commune: its rank (12e arrondissement) when the
 * code gives one, so the indented row reads as a part of the row above; else the served name.
 */
export function nomSousCommune(ligne: { code_insee: string; nom: string }): string {
	return libelleCourtArrondissement(ligne.code_insee) ?? ligne.nom;
}

/** Who an arrondissement shown outside its commune belongs to: arrondissement de Paris. */
export function appartenance(communeAbsente: { nom: string } | null): string | null {
	return communeAbsente ? `arrondissement de ${communeAbsente.nom}` : null;
}
