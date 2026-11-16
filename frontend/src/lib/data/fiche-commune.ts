// Reading of a commune fiche (UI-SPEC « /commune/[code] », « Les quatre états servis »), shared by the
// universal load of `/commune/[code]` and by its open JSON export `/commune/[code].json` (04.7-14):
// the page and the export read the same object, never two requests. Four honest outcomes, never
// confused with one another:
//   - `fiche`           the API serves the commune's synthesis;
//   - `hors-couverture` the API serves no row and says why (`meta.hebergement.commune`, D-24): the
//                       page is titled from the referential (H2) and shows the reason, never a 0;
//   - `non-renseignee`  no row and no reason, but the referential knows the commune;
//   - `panne`           the synthesis did not answer (503 is global, D-47 / CONTEXT): nothing
//                       partial is shown, the title is read from the referential when it answers.
//                       The failure is typed (`detail`): a rate limit or a refused access is not
//                       an outage of the service and the page says so (WR-01).
// A code the referential does not know is a 404 (« INSEE inconnu »), and a malformed code is a 404
// before any request: an unknown code and a commune out of coverage are two different pages.
import { error } from '@sveltejs/kit';
import { ApiError } from '$lib/api/client';
import { getCommuneSynthese } from '$lib/api/commune';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import type { DatasetHosting } from '$lib/api/admin';
import { getCommuneReferentiel } from '$lib/api/communes';
import { COMMUNES_PLM, communeParente, type CommuneParente } from '$lib/data/arrondissements';
import { LIBELLES_MOTIFS } from '$lib/data/etats-libelles';
import { detailPanne, type DetailPanne } from '$lib/data/panne';
import type { OutOfCoverageReason } from '$lib/types/fiche';
import { dateConsultationFr } from '$lib/utils/date-consultation';
import { markOutage, type SetHeaders } from '$lib/utils/etat-page';


/** INSEE code shape: five digits, or 2A/2B + three digits for Corsica. */
export const CODE_INSEE = /^(\d{5}|2[AB]\d{3})$/;

/** Identity of a commune read from the referential (H2); a field not served is null. */
export interface IdentiteCommune {
	nom: string;
	departement: string | null;
	codePostal: string | null;
}

/**
 * The commune an arrondissement belongs to (75112 -> Paris); null for a commune. Read from the
 * served `commune_parent` when the synthesis carries it, else from the code, which fixes the 45
 * arrondissements (the API deduces `type_zone` from the code in the same way).
 */
export function parentDeLaZone(
	code: string,
	servi?: { type_zone?: 'commune' | 'arrondissement' | null; commune_parent?: string | null }
): CommuneParente | null {
	if (servi?.type_zone === 'commune') return null;
	const issu = servi?.commune_parent ? COMMUNES_PLM.find((c) => c.code === servi.commune_parent) : undefined;
	return issu ? { code: issu.code, nom: issu.nom } : communeParente(code);
}

export type FicheCommuneData =
	| {
			etat: 'fiche';
			code: string;
			/** Commune of an arrondissement; null for a commune. */
			parent?: CommuneParente | null;
			synthese: CommuneSynthese;
			sources: SourceAttribution[] | null;
			consulteLe: string;
	  }
	| { etat: 'hors-couverture'; code: string; parent?: CommuneParente | null; identite: IdentiteCommune; motif: OutOfCoverageReason | null }
	| { etat: 'non-renseignee'; code: string; parent?: CommuneParente | null; identite: IdentiteCommune }
	| {
			etat: 'panne';
			code: string;
			parent?: CommuneParente | null;
			identite: IdentiteCommune | null;
			detail: DetailPanne;
			/** Served `Retry-After` of the failure, in seconds; null when none was served. */
			retryAfter: number | null;
	  };

type Fetch = typeof fetch;

async function identiteDuReferentiel(code: string, fetchImpl: Fetch): Promise<IdentiteCommune | null> {
	// The referential lists an arrondissement only on request: asked for the arrondissement codes alone,
	// so the URL of a commune does not change.
	const ligne = await getCommuneReferentiel(code, fetchImpl, { inclureArrondissements: communeParente(code) !== null });
	if (!ligne) return null;
	return { nom: ligne.nom, departement: ligne.departement || null, codePostal: ligne.code_postal ?? null };
}

/** Identity of a commune whose synthesis failed: attempted, never required. */
async function identiteTentee(code: string, fetchImpl: Fetch): Promise<IdentiteCommune | null> {
	try {
		return await identiteDuReferentiel(code, fetchImpl);
	} catch {
		return null;
	}
}

/**
 * Read the fiche of a commune. A page load passes the `setHeaders` of its event: an outage is then
 * marked for `hooks.server.ts`, which answers 503 with `Retry-After` (04.7-10, decision 11) while the
 * page stays rendered. The JSON export has no such event and reads the same object without it.
 */
export async function lireFicheCommune(
	codeDemande: string,
	fetch: Fetch,
	options: { setHeaders?: SetHeaders } = {}
): Promise<FicheCommuneData> {
	const fiche = await lireLaFiche(codeDemande, fetch);
	// Both outage branches below (the synthesis, or the referential after an empty synthesis) end here.
	if (fiche.etat === 'panne') markOutage(options.setHeaders, fiche.retryAfter);
	return fiche;
}

async function lireLaFiche(codeDemande: string, fetch: Fetch): Promise<FicheCommuneData> {
	const code = codeDemande.toUpperCase();
	if (!CODE_INSEE.test(code)) {
		error(404, `Aucune commune ne porte le code INSEE ${codeDemande} dans le référentiel.`);
	}

	const parentCode = parentDeLaZone(code);
	// `/v1/commune` has one outage state (503, whole synthesis): no block is shown partially.
	const enPanne = async (err: unknown): Promise<FicheCommuneData> => ({
		etat: 'panne',
		code,
		parent: parentCode,
		identite: await identiteTentee(code, fetch),
		detail: detailPanne(err),
		retryAfter: err instanceof ApiError ? err.retryAfter : null
	});
	let reponse: Awaited<ReturnType<typeof getCommuneSynthese>>;
	try {
		reponse = await getCommuneSynthese(code, fetch);
	} catch (err) {
		return enPanne(err);
	}
	// A 200 outside the contract (a proxy page, a version skew during a deploy) is an outage too, not an
	// exception that ends on the root error page without a way to reload (review 2026-11-10, R-20).
	if (!Array.isArray(reponse?.donnees)) return enPanne(new Error('/v1/commune: donnees is not a list'));

	const synthese = reponse.donnees[0];
	if (synthese) {
		return {
			etat: 'fiche',
			code,
			parent: parentDeLaZone(code, synthese),
			synthese,
			sources: reponse.meta?.source ?? null,
			// Date of consultation of the printed document (D-12), taken when the data was read.
			consulteLe: dateConsultationFr()
		};
	}

	const meta = reponse.meta as { hebergement?: Record<string, DatasetHosting> } | undefined;
	const hebergement = meta?.hebergement?.commune;
	let identite: IdentiteCommune | null = null;
	let referentielEnPanne = false;
	try {
		identite = await identiteDuReferentiel(code, fetch);
	} catch {
		referentielEnPanne = true;
	}
	const inconnue = () =>
		error(404, `Aucune commune ne porte le code INSEE ${code} dans le référentiel.`);
	if (hebergement?.etat === 'hors_couverture') {
		if (!identite && !referentielEnPanne) inconnue();
		return {
			etat: 'hors-couverture',
			code,
			parent: parentCode,
			// H2: when the referential does not answer, the title is « Commune INSEE {code} ».
			identite: identite ?? { nom: `Commune INSEE ${code}`, departement: null, codePostal: null },
			// Only the three contract reasons (D-24) have a wording; any other is not shown.
			motif: hebergement.motif && Object.hasOwn(LIBELLES_MOTIFS, hebergement.motif) ? hebergement.motif : null
		};
	}
	// Without a reason and without the referential, whether the commune exists is unknown.
	if (referentielEnPanne) {
		return { etat: 'panne', code, parent: parentCode, identite: null, detail: { statut: null, titre: null, message: null }, retryAfter: null };
	}
	if (!identite) return inconnue();
	return { etat: 'non-renseignee', code, parent: parentCode, identite };
}
