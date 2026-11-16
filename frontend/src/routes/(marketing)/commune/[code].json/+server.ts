// Open JSON export of a commune fiche (04.7-14, Origami R5): `/commune/{code}.json`, no key for the
// visitor (the site reads the API server side with its public key, as for the page). It serves the
// object the page renders (`lireFicheCommune`), so the export never contradicts the page (A1), with
// the sources and their licences. The four outcomes stay apart:
//   - fiche, hors couverture, non renseignée: 200 with `etat` (an absence is never a zero);
//   - unknown or malformed code: 404 `application/problem+json`, the reading's own message, with
//     the headers of the export so that another site can read it;
//   - failure: `application/problem+json`, never an empty object. A rate limit or a monthly quota of
//     the API is a 429 with its typed title and the served `Retry-After`; a refused access keeps its
//     typed title as a 503; only a plain outage (5xx, network) gets the default retry of a minute.
// Not indexed (the page is the indexed document), cacheable for an hour, readable from any origin.
import { isHttpError, json } from '@sveltejs/kit';
import { lireFicheCommune } from '$lib/data/fiche-commune';
import { TITRE_LIMITE } from '$lib/data/panne';
import type { RequestHandler } from './$types';

export const prerender = false;

const FORMAT_EXPORT_COMMUNE = 'datacommune.commune.v1';

const LICENCE =
	'Chaque valeur reste sous la licence de son producteur, nommée dans « sources » ; une réutilisation cite la source. Mentionner DataCommune, avec le lien de la page, est bienvenu sans être une condition.';

const ENTETES_COMMUNS = {
	'access-control-allow-origin': '*',
	'x-robots-tag': 'noindex'
};

/** A problem document of the export (RFC 9457), with the headers every answer of the export carries. */
function probleme(status: number, title: string, detail: string, page: string, entetes: Record<string, string> = {}) {
	return json(
		{ type: 'about:blank', status, title, detail, page },
		{
			status,
			headers: {
				...ENTETES_COMMUNS,
				'content-type': 'application/problem+json; charset=utf-8',
				'cache-control': 'no-store',
				...entetes
			}
		}
	);
}

export const GET: RequestHandler = async ({ params, fetch, url }) => {
	let fiche: Awaited<ReturnType<typeof lireFicheCommune>>;
	try {
		fiche = await lireFicheCommune(params.code, fetch);
	} catch (erreur) {
		if (isHttpError(erreur) && erreur.status === 404) {
			return probleme(404, 'Commune inconnue', erreur.body.message, `${url.origin}/commune/${encodeURIComponent(params.code)}`);
		}
		throw erreur;
	}
	const page = `${url.origin}/commune/${encodeURIComponent(fiche.code)}`;
	const luLe = new Date().toISOString();

	if (fiche.etat === 'panne') {
		const { statut, titre, message } = fiche.detail;
		const delai: Record<string, string> = fiche.retryAfter != null && fiche.retryAfter > 0 ? { 'retry-after': String(fiche.retryAfter) } : {};
		if (statut === 429) return probleme(429, titre ?? TITRE_LIMITE, message ?? TITRE_LIMITE, page, delai);
		if (statut !== null && statut >= 400 && statut < 500) {
			return probleme(503, titre ?? 'Source indisponible', message ?? 'La synthèse de la commune a été refusée.', page);
		}
		return probleme(
			503,
			'Source indisponible',
			message ?? "La synthèse de la commune n'a pas répondu. Ce n'est pas une absence de données ; réessayez dans un instant.",
			page,
			Object.keys(delai).length ? delai : { 'retry-after': '60' }
		);
	}

	const entete = { format: FORMAT_EXPORT_COMMUNE, code_insee: fiche.code, page, lu_le: luLe, licence: LICENCE };
	const corps =
		fiche.etat === 'fiche'
			? { ...entete, etat: 'fiche', sources: fiche.sources ?? [], donnees: fiche.synthese }
			: fiche.etat === 'hors-couverture'
				? { ...entete, etat: 'hors_couverture', commune: fiche.identite, motif: fiche.motif, donnees: null }
				: { ...entete, etat: 'non_renseignee', commune: fiche.identite, donnees: null };

	return json(corps, {
		headers: { ...ENTETES_COMMUNS, 'cache-control': 'public, max-age=3600' }
	});
};
