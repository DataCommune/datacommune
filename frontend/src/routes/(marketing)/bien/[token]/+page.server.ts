// Legacy `/bien/[token]` links (D-101, D-102; 04-UI-SPEC « Redirections /bien »). The route
// renders nothing: every token ends in a redirection (308, 307 on an outage) to an internal path built here, never to a value
// taken from the token or from an API answer as is (T-04-08-01).
//   - A legacy coordinate token goes to `/` without any lookup: a precise position is a location
//     vector, it never reaches a target, a request or a log (T-04-08-02, anti-PII D-04).
//   - An address slug is read back as a label (`deslugifyAdresse`) and searched on this
//     instance's BAN (`searchAdresses`, `limite=2`, fetch policy of the 04-17 loaders). Exactly one
//     served address whose label normalizes to the token's → `/adresse/{ban_id}`, the key
//     re-checked against the 04-02 pattern. Anything else — zero, several, a street, an outage, a
//     refused or malformed key — goes to the search, `/?q={label}`.
//   - An outage of the search (error, timeout) is a passing state: its redirection is a temporary
//     307, never a cacheable 308 that would keep the link on the search afterwards (C17).
// `?dpe=`, `?origine=` and `?type_bien=` are ignored. The label is never logged (T-47-02).
import { redirect } from '@sveltejs/kit';
import { BAN_ID_RE, searchAdresses, type ResultatRechercheAdresse } from '$lib/api/adresses';
import { libelleCommune, libelleVoie } from '$lib/data/recherche-lieux';
import { fetchApiDeChargeur } from '$lib/server/api-route-fetch';
import { deslugifyAdresse, slugifyAdresse } from '$lib/utils/slug';
import type { PageServerLoad } from './$types';

export const prerender = false;

const LEGACY_COORDS = /^-?\d+(?:\.\d*)?,-?\d+(?:\.\d*)?$/;
// Bounds of `q` on `GET /v1/adresses/recherche`: a label outside them is not sent (422 otherwise).
const Q_MIN = 2;
const Q_MAX = 200;

/** Slug of the served label « {numéro}{indice} {voie}, {CP} {Commune} » of a search result. */
function slugResultat(result: ResultatRechercheAdresse): string {
	return slugifyAdresse(
		[libelleVoie(result.numero, result.rep, result.nom_voie), libelleCommune(result.code_postal, result.nom_commune)].join(' ')
	);
}

/** Outage of the search: not a page, the visitor lands on the search with the label (C17: 307). */
const PANNE = Symbol('panne');

/** The BAN key of the single served address matching *libelle* exactly, null, or an outage. */
async function adresseUnique(libelle: string, fetchImpl: typeof fetch): Promise<string | null | typeof PANNE> {
	if (libelle.length < Q_MIN || libelle.length > Q_MAX) return null;
	let resultats: ResultatRechercheAdresse[];
	try {
		resultats = (await searchAdresses(libelle, { limite: 2 }, fetchImpl)).resultats ?? [];
	} catch {
		return PANNE;
	}
	const attendu = slugifyAdresse(libelle);
	const exactes = resultats.filter((result) => result.type_resultat === 'adresse' && slugResultat(result) === attendu);
	if (exactes.length !== 1) return null;
	const banId = exactes[0].ban_id;
	return typeof banId === 'string' && BAN_ID_RE.test(banId) ? banId : null;
}

export const load: PageServerLoad = async ({ params, request, platform, getClientAddress }) => {
	if (LEGACY_COORDS.test(params.token)) redirect(308, '/');
	const libelle = deslugifyAdresse(params.token);
	if (libelle === '') redirect(308, '/');
	const banId = await adresseUnique(libelle, fetchApiDeChargeur(request, platform, getClientAddress));
	if (banId === PANNE) redirect(307, `/?q=${encodeURIComponent(libelle)}`);
	if (banId !== null) redirect(308, `/adresse/${encodeURIComponent(banId)}`);
	redirect(308, `/?q=${encodeURIComponent(libelle)}`);
};
