// The address page reads its identity from `GET /v1/adresse/{ban_id}/rapport` (04.7-03). The specs
// written for the former `/v1/adresse/{ban_id}` answer keep their identity fixtures: `enRapport`
// carries that identity and coverage into a report that serves no theme, so the lists of the page
// (documents, sales, similar sales) render on their own. An error answer is relayed as is.

type Reponse = { status: number; body: unknown; gate?: string };

export const DATE_RAPPORT = '2026-10-30';

/** One argument only: it is passed to `Array.map`, whose index must never become the themes. */
export function enRapport<R extends Reponse>(reponse: R): R {
	return rapportAvecThemes(reponse, []);
}

/** The same identity with *themes*, the report themes to serve. */
export function rapportAvecThemes<R extends Reponse>(reponse: R, themes: unknown[]): R {
	const corps = reponse.body as { donnees?: { identite?: unknown; couverture?: unknown }; meta?: unknown } | null;
	if (reponse.status !== 200 || !corps?.donnees) return reponse;
	return {
		...reponse,
		body: {
			statut: 'ok',
			donnees: {
				adresse: corps.donnees.identite,
				couverture: corps.donnees.couverture,
				date: DATE_RAPPORT,
				resume: { points_adresse: [], points_commune: [], rassurants: [], chiffres_cles: [] },
				themes
			},
			...(corps.meta ? { meta: corps.meta } : {})
		}
	};
}

/** The same, for the fixtures that carry a body rather than a reply. */
export const corpsRapport = (body: unknown): unknown => enRapport({ status: 200, body }).body;

/** Path of the report of an address. */
export const cheminRapport = (banId: string): string => `/v1/adresse/${banId}/rapport`;

/**
 * Query of the report as the fiche asks it (R-07): the short wait, so that a slow source is said
 * « en cours » instead of holding the page. Its follow-up asks `attente=complete`, and only a fiche
 * served with a read in progress asks for it.
 */
export const REQUETE_RAPPORT: Record<string, string> = { attente: 'courte' };
