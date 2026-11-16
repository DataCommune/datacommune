// The gate both loaders of the address fiche pass before they read the API (R-16 of the review of
// 2026-11-10). The identity (`+layout.server.ts`) and the lists (`+page.server.ts`) are two nodes: a
// page change of a list requests the page node alone (`x-sveltekit-invalidated=…01`), so a gate on the
// layout only let the lists of any fiche be read without spending the allowance.
import { error } from '@sveltejs/kit';
import { BAN_ID_RE } from '$lib/api/adresses';
import { TITRE_ADRESSE_INCONNUE } from '$lib/data/recherche-lieux';
import { admettreFiche, type EvenementFiche } from '$lib/server/fiche-quota';

const DATE_REPRISE = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', timeZone: 'Europe/Paris' });

/**
 * Throws 404 for a key of a form the API refuses (IN-12) and 429 once the session has spent its weekly
 * allowance (H-3), before anything is read: a fiche refused by the allowance costs no API call.
 * Returns the give-back of the place this request counted (R-16, option B): the identity calls it when
 * the key is not a fiche (unknown, a street) or when our side failed; it does nothing for a fiche the
 * session had already counted, and nothing the second time.
 */
export async function admettreOuRefuser(event: EvenementFiche, id: string): Promise<() => void> {
	if (!BAN_ID_RE.test(id)) error(404, TITRE_ADRESSE_INCONNUE);
	const admission = await admettreFiche(event, id);
	if (!admission.admise) {
		error(
			429,
			`Vous avez consulté ${admission.limite} fiches d'adresse différentes cette semaine, la limite de la consultation gratuite. ` +
				`Les fiches déjà ouvertes restent consultables ; les autres le seront de nouveau le ${DATE_REPRISE.format(admission.reprise)}.`
		);
	}
	return () => admission.rendre?.();
}
