// The dialog of the parcel map, out of the first view of the address fiche (plan 04.10-12: 16 404
// bytes gzip of the fiche were the dialog, paid by every visitor whether the map was opened or not).
// One request shared by the warm-up (pointer or focus on the button) and the click.
//
// A dynamic import that fails is kept failed by the browser for the life of the page (seen with
// Leaflet at plan 06): no retry is attempted here, the caller says the failure and the visitor
// reloads the page. A failed attempt is not kept, so a later click asks again where a browser allows it.
import type ParcelMapDialog from './ParcelMapDialog.svelte';

let enCours: Promise<typeof ParcelMapDialog> | null = null;

export function chargerDialogue(): Promise<typeof ParcelMapDialog> {
	enCours ??= import('./ParcelMapDialog.svelte').then(
		(module) => module.default,
		(erreur: unknown) => {
			enCours = null;
			throw erreur;
		}
	);
	return enCours;
}
