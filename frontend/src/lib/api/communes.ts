import { api } from './client';
import type { DatasetHosting } from './admin';
import type { ResultatRechercheAdresse } from './adresses';
import type { EtatColonne, SourceAttribution } from './commune';

// --- Types (mirror backend Pydantic schemas in app/zone/schemas.py) ---

/**
 * One row of `GET /v1/communes`. The population follows the rule of the search and of the commune
 * page (H6): `population_annee` is null when the value comes from the `geo_communes` referential,
 * whose year is unknown; never invent one.
 */
export interface CommuneListRow {
	code_insee: string;
	nom: string;
	departement: string;
	population: number | null;
	population_annee: number | null;
	population_source: 'demographie' | 'geo_communes' | null;
	/** Served with the `code_insee` filter only (BAN hosted for the departement), else null. */
	code_postal?: string | null;
	/** `arrondissement` for a municipal arrondissement of Paris, Lyon or Marseille; absent means a commune. */
	type_zone?: 'commune' | 'arrondissement';
	/** INSEE code of the commune of an arrondissement (75112 -> 75056); null for a commune. */
	commune_parent?: string | null;
}

/**
 * One result of `GET /v1/communes/search` (D-42). `population_annee` is null when the value
 * comes from the `geo_communes` referential, whose year is unknown (H6): never invent one.
 * `hebergement` is the hosting `/v1/commune` would serve for this code: a commune out of the
 * instance's coverage is listed with its reason, never excluded (D-44).
 */
export interface CommuneSearchResult {
	code_insee: string;
	nom: string;
	code_postal: string | null;
	departement: string;
	population: number | null;
	population_annee: number | null;
	population_source: 'demographie' | 'geo_communes' | null;
	hebergement: DatasetHosting;
	/** `arrondissement` for a municipal arrondissement of Paris, Lyon or Marseille; absent means a commune. */
	type_zone?: 'commune' | 'arrondissement';
	/** INSEE code of the commune of an arrondissement (75112 -> 75056); null for a commune. */
	commune_parent?: string | null;
}

/**
 * Option model of the search field (D-45, D-106): a discriminated union on `kind`. Communes, and
 * since 04-17 the BAN results of `/v1/adresses/recherche` (a numbered address or a street without
 * number, told apart by `type_resultat`). No member is declared for a capability the API does not
 * serve yet (DPE numbers stay out of the field, UI-SPEC Q5).
 */
export type SearchSuggestion =
	| { kind: 'commune'; result: CommuneSearchResult }
	| { kind: 'adresse'; result: ResultatRechercheAdresse };

export interface CommunesListResponse {
	results: CommuneListRow[];
	total_count: number;
	page: number;
	per_page: number;
	has_next: boolean;
}

/**
 * One row of `GET /v1/departements`. Since 04.7-10 a row also says the state of the pages of the
 * departement, computed by the API (never a territory written here): the sitemap and the
 * `/departements` page read `indexable` and `prix`, nothing else decides.
 */
export interface DepartementRow {
	code: string;
	nom: string;
	/** Official name of the region, accented (« Île-de-France »). */
	region: string | null;
	/**
	 * Day (AAAA-MM-JJ, Paris time) of the last successful import of the sources of the page that are
	 * hosted AND imported for this departement; null when none is. The date the data served changed,
	 * never the date of the answer nor the period of the data.
	 */
	derniere_importation: string | null;
	/** Hosting of DVF, the source of the prices, for this departement, with the reason when out of coverage. */
	prix: DatasetHosting;
	/** DVF hosted AND imported for this departement: its page and its fiches enter the sitemap. */
	indexable: boolean;
}

/** Sum of the populations of the communes of a departement (never of their arrondissements). */
export interface PopulationDepartement {
	total: number | null;
	communes_comptees: number | null;
	/** Vintage common to the communes summed; null when their years differ or the sum is absent. */
	annee: number | null;
	/** State of an absent sum; null when it is served. */
	etat: EtatColonne | null;
}

/** Median price per m² of one type of dwelling over the departement. */
export interface PrixTypeDepartement {
	mediane_m2: number | null;
	/** Sales retained over the window; served with the median or under the threshold, null for none (never 0). */
	effectif: number | null;
	etat: EtatColonne | null;
}

export interface PrixDepartement {
	/** The 12 months of the medians, bounds included; null when no sale is served. */
	fenetre: { debut: string; fin: string } | null;
	maison: PrixTypeDepartement;
	appartement: PrixTypeDepartement;
}

export type ClasseDpe = 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G';

/** DPE documents of the departement, the documentary population of the commune pages. */
export interface DpeDepartement {
	classes: Record<ClasseDpe, number> | null;
	inconnue: number | null;
	total: number | null;
	classes_connues: number | null;
	/** Share of the documents classed F or G among those of a known class, in percentage points. */
	part_fg_pct: number | null;
	date_min: string | null;
	date_max: string | null;
	/** State of an absent block; null when the counts are served. */
	etat: EtatColonne | null;
	/** State of an absent share: the block's, or `zero_denominator` when no class is known. */
	etat_part_fg: EtatColonne | null;
}

/** One of the most populated zones of a departement (its arrondissements for Paris). */
export interface CommunePrincipale {
	code_insee: string;
	nom: string;
	type_zone: 'commune' | 'arrondissement';
	commune_parent: string | null;
	population: number | null;
	population_annee: number | null;
	population_source: 'demographie' | 'geo_communes' | null;
}

/** `GET /v1/departements/{code}`: the key figures of a departement, each block with its own state. */
export interface DepartementSynthese {
	code: string;
	nom: string;
	region: string | null;
	derniere_importation: string | null;
	indexable: boolean;
	/** Hosting of dvf, dpe and demographie for this departement. */
	couverture: Record<string, DatasetHosting>;
	communes: { nombre: number; arrondissements: number };
	population: PopulationDepartement;
	prix: PrixDepartement;
	dpe: DpeDepartement;
	communes_principales: CommunePrincipale[];
}

/** Envelope of `GET /v1/departements/{code}`: the figures and the attribution of their sources. */
export interface ReponseDepartementSynthese {
	donnees: DepartementSynthese;
	meta?: { source?: SourceAttribution | SourceAttribution[]; hebergement?: Record<string, DatasetHosting> | null } | null;
}

/** `GET /v1/departements/{code}/fiches`: every page of a departement in one call, for the sitemap. */
export interface FichesDepartement {
	code: string;
	/** Day of the last import of the sources of the fiches that are hosted AND imported here; null when none. */
	derniere_importation: string | null;
	fiches: FicheDepartementRow[];
}

/** A page of a departement: its code, its name in the referential, its kind. */
export interface FicheDepartementRow {
	code_insee: string;
	nom: string;
	type_zone: 'commune' | 'arrondissement';
}

/** `GET /v1/communes/voisines`: the pages near a commune page, bordering ones first. */
export interface CommunesVoisines {
	code_insee: string;
	voisines: {
		code_insee: string;
		nom: string;
		departement_code: string;
		type_zone: 'commune' | 'arrondissement';
		/** The contours of the referential touch (within 0.001 degree). */
		limitrophe: boolean;
	}[];
}

// Backend wraps data routers in the ReponseApi envelope.
interface ReponseApi<T> {
	statut: string;
	donnees: T;
	meta?: HebergementRecherche | null;
}

/**
 * Metadata of a search answer: `hebergement` names, per catalogue key, the coverage of the
 * search's territory (D-24). A postal-code search always serves `hebergement.ban` (CR-01).
 */
export interface HebergementRecherche {
	hebergement?: Record<string, DatasetHosting> | null;
}

/** Envelope of `GET /v1/communes/search`: the results and the metadata that qualifies them. */
export interface CommunesSearchResponse {
	donnees: CommuneSearchResult[];
	meta?: HebergementRecherche | null;
}

// --- API functions ---

export interface SearchCommunesOptions {
	departement?: string;
	limit?: number;
	/**
	 * Adds the municipal arrondissements of the hosted perimeter (`type_zone`, `commune_parent`).
	 * Off by default, as on the API: a commune is never added to its arrondissements.
	 */
	inclureArrondissements?: boolean;
}

/** A code served twice keeps its first row: Paris is never listed twice. */
export function sansDoublon<T extends { code_insee: string }>(rows: readonly T[]): T[] {
	const vus = new Set<string>();
	return rows.filter((r) => (vus.has(r.code_insee) ? false : (vus.add(r.code_insee), true)));
}

/**
 * The only commune search of the browser (D-42): this instance's API, never a third-party
 * origin. Matching (accents, case, hyphens, spaces) is done in SQL: `q` is sent as typed.
 * `signal` lets the caller abort a superseded request so a stale answer is never rendered.
 * The envelope is returned, not only `donnees`: the metadata carries the state of a
 * postal-code search (out of coverage, or a zero bounded to the served departements, CR-01).
 */
export async function searchCommunes(
	q: string,
	options: SearchCommunesOptions = {},
	fetchImpl: typeof fetch = fetch,
	signal?: AbortSignal
): Promise<CommunesSearchResponse> {
	const sp = new URLSearchParams({ q });
	if (options.departement) sp.set('departement', options.departement);
	if (options.limit !== undefined) sp.set('limit', String(options.limit));
	if (options.inclureArrondissements) sp.set('inclure_arrondissements', 'true');
	const res = await api<ReponseApi<CommuneSearchResult[]>>(
		`/v1/communes/search?${sp.toString()}`,
		signal ? { signal } : {},
		fetchImpl
	);
	return { donnees: sansDoublon(res.donnees), meta: res.meta };
}

/**
 * Referential row of one commune, hosted or not (H2): name, departement and, when the BAN is
 * hosted for it, its postal code. `null` when no commune carries this INSEE code — which is
 * distinct from a commune out of coverage.
 */
export async function getCommuneReferentiel(
	code: string,
	fetchImpl: typeof fetch = fetch,
	options: { inclureArrondissements?: boolean } = {}
): Promise<CommuneListRow | null> {
	const sp = new URLSearchParams({ code_insee: code });
	// An arrondissement is in the referential only on request (it is not a commune).
	if (options.inclureArrondissements) sp.set('inclure_arrondissements', 'true');
	const res = await api<ReponseApi<CommunesListResponse>>(
		`/v1/communes?${sp.toString()}`,
		{},
		fetchImpl
	);
	return res.donnees.results[0] ?? null;
}

/**
 * Median price per m² of one type of dwelling in the directory (04.6-02), as the synthesis serves
 * it: `etat` is the served state of a null median (D-47), null exactly when the median is served.
 * `effectif` is the served count of retained sales; null means no DVF row (`not_reported`), never 0.
 */
export interface PrixTypeAnnuaire {
	mediane_m2: number | null;
	effectif: number | null;
	etat: EtatColonne | null;
}

/** One commune of `GET /v1/communes/annuaire`. `prix` is null only when the price read failed. */
export interface CommuneAnnuaireRow {
	code_insee: string;
	nom: string;
	departement: string;
	population: number | null;
	population_annee: number | null;
	population_source: 'demographie' | 'geo_communes' | null;
	hebergement: DatasetHosting;
	prix: { maison: PrixTypeAnnuaire; appartement: PrixTypeAnnuaire } | null;
	/** `arrondissement` for a municipal arrondissement of Paris, Lyon or Marseille; absent means a commune. */
	type_zone?: 'commune' | 'arrondissement';
	/** INSEE code of the commune of an arrondissement (75112 -> 75056); null for a commune. */
	commune_parent?: string | null;
}

/** State of the price read of a directory page, said once for the whole page. */
export interface PrixAnnuaire {
	etat: 'servi' | 'indisponible' | 'sans_lecture';
	motif: string | null;
	fenetre_mediane_dvf: string | null;
}

export interface CommunesAnnuaireResponse {
	results: CommuneAnnuaireRow[];
	total_count: number;
	page: number;
	per_page: number;
	has_next: boolean;
	couverture: 'servies' | 'toutes';
	departements_servis: string[];
	prix: PrixAnnuaire;
}

export interface AnnuaireParams {
	nom?: string;
	departement?: string;
	population_min?: number;
	population_max?: number;
	couverture?: 'servies' | 'toutes';
	sort_by?: 'nom' | 'population';
	order?: 'asc' | 'desc';
	/** Adds the arrondissements under their commune (their prices are their own, never summed). */
	inclure_arrondissements?: boolean;
	page?: number;
	per_page?: number;
}

/**
 * The directory of communes with their served prices (04.6-02); filters apply to the total.
 * `fetchImpl`: the universal `load` passes SvelteKit's fetch so the page renders server-side.
 */
export async function listAnnuaire(
	params: AnnuaireParams,
	fetchImpl: typeof fetch = fetch
): Promise<CommunesAnnuaireResponse> {
	const sp = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== '') sp.set(key, String(value));
	}
	const qs = sp.toString();
	const res = await api<ReponseApi<CommunesAnnuaireResponse>>(
		`/v1/communes/annuaire${qs ? '?' + qs : ''}`,
		{},
		fetchImpl
	);
	return res.donnees;
}

export async function listDepartements(
	fetchImpl: typeof fetch = fetch
): Promise<DepartementRow[]> {
	const res = await api<ReponseApi<DepartementRow[]>>('/v1/departements', {}, fetchImpl);
	return res.donnees;
}

/**
 * Key figures of one departement. The caller passes a code it has validated (the API answers 422
 * to a malformed one and 404 to one the referential does not know); `2a` is read `2A` by the API.
 * The whole route is a 503 when the commune chain is not published: nothing partial is served.
 */
export async function getDepartementSynthese(
	code: string,
	fetchImpl: typeof fetch = fetch
): Promise<ReponseDepartementSynthese> {
	return api<ReponseDepartementSynthese>(`/v1/departements/${encodeURIComponent(code)}`, {}, fetchImpl);
}

/** Every commune and arrondissement page of a departement, sorted by code (never truncated). */
export async function getDepartementFiches(
	code: string,
	fetchImpl: typeof fetch = fetch
): Promise<FichesDepartement> {
	const res = await api<ReponseApi<FichesDepartement>>(
		`/v1/departements/${encodeURIComponent(code)}/fiches`,
		{},
		fetchImpl
	);
	return res.donnees;
}

/** The pages near a commune or arrondissement page, for its links (at most 8). */
export async function getCommunesVoisines(code: string, fetchImpl: typeof fetch = fetch): Promise<CommunesVoisines> {
	const res = await api<ReponseApi<CommunesVoisines>>(
		`/v1/communes/voisines?code_insee=${encodeURIComponent(code)}`,
		{},
		fetchImpl
	);
	return res.donnees;
}
