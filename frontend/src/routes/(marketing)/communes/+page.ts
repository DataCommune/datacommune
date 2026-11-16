// Universal load of `/communes` (UI-SPEC « /communes », H5). Two modes, both server-rendered so
// the URL is shareable and the page works without JavaScript:
//   - `q` (2 characters or more): commune search cards from `/v1/communes/search` (D-42), sorted
//     by served population, the departement filter kept;
//   - `q` empty: the directory of `/v1/communes/annuaire` (04.6-02), served departements by
//     default, filters in the URL (`annuaire-communes.ts`); the departement is optional.
// A `q` of one character sends no request. An empty search has three distinct states: a
// postal-code search out of coverage (typed, with its reason), a zero bounded to the served
// departements, and an outage (« Panne recherche », a failed call) — « aucun résultat » never
// says « n'existe pas », and none of them is shown as another (CR-01, SEM-01). A rate limit or a
// refused access is not an outage of the service: the failure is typed and the screen says so
// (WR-01, `detailPanne`).
// Since 04-17 (UI-SPEC « /communes?q= — résultats typés », D-105, D-106) a search also reads the
// addresses of this instance's BAN, in parallel: two typed groups in the order served by the
// address search (`ordre_types`). The failure of one search is the typed state of its group, the
// other group stays; both failed is the page outage above.
// Reading `url` makes SvelteKit re-run this load on every query-string change.
import { ApiError } from '$lib/api/client';
import { listAnnuaire, searchCommunes } from '$lib/api/communes';
import type { CommunesAnnuaireResponse, CommuneSearchResult } from '$lib/api/communes';
import {
	searchAdresses,
	type CorrespondanceNumeroRecherche,
	type HebergementBanRecherche,
	type ResultatRechercheAdresse,
	type TypeResultat
} from '$lib/api/adresses';
import { lireFiltresAnnuaire, parametresApi, type FiltresAnnuaire } from '$lib/data/annuaire-communes';
import { DEPARTEMENTS } from '$lib/data/departements';
import { detailPanne, type DetailPanne } from '$lib/data/panne';
import { couvertureCodePostal, type CouvertureCodePostal } from '$lib/data/recherche-code-postal';
import { prefixesProches, variantesOrthographe } from '$lib/data/recherche-lieux';
import { markOutage } from '$lib/utils/etat-page';
import type { PageLoad } from './$types';

export const prerender = false;

const SEARCH_LIMIT_STEP = 20;
const SEARCH_LIMIT_MAX = 50; // `limit` ceiling of /v1/communes/search
const ADRESSES_LIMITE = 8; // `limite` ceiling of /v1/adresses/recherche
const ORDRE_PAR_DEFAUT: TypeResultat[] = ['commune', 'adresse'];

/**
 * Next step of a search that found no commune (04.7-14, R16): other spellings to try, and the
 * communes whose name starts with a shortened prefix, read only then. Never the result itself.
 */
export type SuiteRecherche = {
	orthographes: string[];
	proches: { prefixe: string; communes: CommuneSearchResult[] } | null;
};

/** The directory page: its filters (read from the URL) and the served answer, null on failure. */
export type Annuaire = { filtres: FiltresAnnuaire; reponse: CommunesAnnuaireResponse | null };

/** The address group of a search: the served results and BAN hosting, or its typed failure. */
export type GroupeAdresses =
	| {
			etat: 'ok';
			resultats: ResultatRechercheAdresse[];
			hebergement: HebergementBanRecherche;
			correspondanceNumero: CorrespondanceNumeroRecherche | null;
	  }
	| { etat: 'panne'; panne: DetailPanne }
	/** Served 200 `etat: 'trop_large'` (R-08): too broad to be searched to the end, no result, no zero. */
	| { etat: 'trop_large'; hebergement: HebergementBanRecherche };

export type CommunesPageData = {
	mode: 'trop-court' | 'recherche' | 'annuaire';
	q: string;
	departement: string;
	limite: number;
	results: CommuneSearchResult[];
	/** Directory mode only (`q` empty); null otherwise. */
	annuaire: Annuaire | null;
	/** The failed call, typed (rate limit, refused access, outage); null when it answered. */
	panne: DetailPanne | null;
	/** State of a postal-code search, read in the served meta; null otherwise. */
	couverture: CouvertureCodePostal;
	/** Address group of a search (04-17); null when no address search was made. */
	adresses: GroupeAdresses | null;
	/** Order of the groups, as served by the address search (D-106); never computed here. */
	ordre: TypeResultat[];
	/** Next step proposed when no commune was found; null otherwise. */
	suite: SuiteRecherche | null;
};

function parseLimite(raw: string | null): number {
	const value = Math.trunc(Number(raw ?? SEARCH_LIMIT_STEP)) || SEARCH_LIMIT_STEP;
	return Math.min(SEARCH_LIMIT_MAX, Math.max(SEARCH_LIMIT_STEP, value));
}

/**
 * Communes « au nom proche »: the shortened prefixes are read one after the other until one answers
 * with communes (two reads at most). A failed read proposes nothing: the suggestion is never a result,
 * so nothing here is said to be absent.
 */
async function suiteRecherche(q: string, departement: string | undefined, fetchImpl: typeof fetch): Promise<SuiteRecherche> {
	let proches: SuiteRecherche['proches'] = null;
	for (const prefixe of prefixesProches(q)) {
		try {
			const reponse = await searchCommunes(prefixe, { departement, limit: 5, inclureArrondissements: true }, fetchImpl);
			if (reponse.donnees.length > 0) {
				proches = { prefixe, communes: reponse.donnees };
				break;
			}
		} catch {
			break;
		}
	}
	return { orthographes: variantesOrthographe(q), proches };
}

/** The delay a failed call served, for the `Retry-After` of the outage page. */
const retryAfterDe = (err: unknown): number | null => (err instanceof ApiError ? err.retryAfter : null);

// `setHeaders` marks an outage of the page for the 503 of hooks.server.ts (04.7-10, decision 11; review
// R-21); it does nothing in the browser. The failure of the address group alone is not one: the communes
// are served.
export const load: PageLoad = async ({ url, fetch, setHeaders }): Promise<CommunesPageData> => {
	const params = url.searchParams;
	const q = (params.get('q') ?? '').trim();
	// The filter list is the static referential of every French departement: an unknown code
	// is dropped rather than sent. It says nothing about what this instance hosts (D-34).
	const rawDepartement = (params.get('departement') ?? '').trim().toUpperCase();
	const departement = DEPARTEMENTS.some((d) => d.code === rawDepartement) ? rawDepartement : '';
	const limite = parseLimite(params.get('limite'));
	const base = {
		q,
		departement,
		limite,
		results: [],
		annuaire: null,
		panne: null,
		couverture: null,
		adresses: null,
		ordre: ORDRE_PAR_DEFAUT,
		suite: null
	};

	if (q.length === 1) return { ...base, mode: 'trop-court' };

	if (q.length >= 2) {
		const filtre = departement || undefined;
		const [communes, adresses] = await Promise.allSettled([
			searchCommunes(q, { departement: filtre, limit: limite, inclureArrondissements: true }, fetch),
			searchAdresses(q, { departement: filtre, limite: ADRESSES_LIMITE }, fetch)
		]);
		const groupeAdresses: GroupeAdresses =
			adresses.status === 'fulfilled' && adresses.value.etat === 'trop_large'
				? { etat: 'trop_large', hebergement: adresses.value.hebergement.ban }
				: adresses.status === 'fulfilled'
				? {
						etat: 'ok',
						resultats: adresses.value.resultats,
						hebergement: adresses.value.hebergement.ban,
						correspondanceNumero: adresses.value.correspondance_numero ?? null
					}
				: { etat: 'panne', panne: detailPanne(adresses.reason) };
		const ordre = adresses.status === 'fulfilled' ? adresses.value.ordre_types : ORDRE_PAR_DEFAUT;
		if (communes.status === 'rejected') {
			markOutage(setHeaders, retryAfterDe(communes.reason));
			return { ...base, mode: 'recherche', panne: detailPanne(communes.reason), adresses: groupeAdresses, ordre };
		}
		const couverture = couvertureCodePostal(q, communes.value.meta);
		const suite =
			communes.value.donnees.length === 0 && couverture?.kind !== 'hors-couverture'
				? await suiteRecherche(q, filtre, fetch)
				: null;
		return {
			...base,
			mode: 'recherche',
			results: communes.value.donnees,
			couverture,
			adresses: groupeAdresses,
			ordre,
			suite
		};
	}

	// No search: the directory. `departement` is one of its filters, optional (04.6-02).
	const filtres = lireFiltresAnnuaire(params);
	try {
		const reponse = await listAnnuaire(parametresApi(filtres), fetch);
		return { ...base, mode: 'annuaire', annuaire: { filtres, reponse } };
	} catch (err) {
		markOutage(setHeaders, retryAfterDe(err));
		return { ...base, mode: 'annuaire', annuaire: { filtres, reponse: null }, panne: detailPanne(err) };
	}
};
