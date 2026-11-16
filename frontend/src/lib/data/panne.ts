// What a failed API call means for a screen (WR-01, 03-17 B C1/C2/C4, A T-8). `api()` throws an
// `ApiError` carrying the HTTP status, the server title, `Retry-After` and whether the call was
// anonymous. Every 4xx means the service answered: it is a rate limit, a quota, a refused access
// or a refused request, never an outage, and the screen gives it a title and a message of its own.
//
// « indisponible » and « n'a pas répondu » are reserved for 5xx and network failures (SEM-01, X7):
// for those, `titre` and `message` are null and the notice keeps its generic text.
//
// A 429 has four causes on the server (03-17 D-15 G1), and only one of them is a quota:
// - the per-IP ceiling (app/rate_limit.py), with `Retry-After`, signed in or not;
// - the per-minute limit of the key (auth/dependencies.py, bucket `plan:{key}`), without
//   `Retry-After`, the shared public key of anonymous visitors included;
// - the monthly quota (auth/dependencies.py, title « Quota mensuel epuise »), without `Retry-After`;
// - the per-route ceilings of the account routes (auth/router.py, « Trop de demandes »).
// So a 429 is a « limite de requêtes » (with the served delay when `Retry-After` is there), and
// the monthly quota is named only when the server title says so; « votre offre » only for a
// signed-in visitor, the anonymous one uses the shared public key.
//
// The rendered title and message are always constants of the client, never a text of the server
// body; only the numeric status and the served delay may appear. Spaces before `;` and `!` in
// these strings are narrow no-break spaces (U+202F), between a number and its unit no-break
// spaces (U+00A0).
import { ApiError } from '$lib/api/client';
import { ACCOUNTS_ENABLED } from '$lib/config';

export interface DetailPanne {
	/** HTTP status of the failure; null when the call failed before any response (network). */
	statut: number | null;
	/** Typed title of the notice; null for a generic outage (5xx, network). */
	titre: string | null;
	/** Typed message to render instead of the generic outage text; null for a generic outage. */
	message: string | null;
}

export const TITRE_LIMITE = 'Trop de requêtes pour le moment';
export const TITRE_QUOTA = 'Quota mensuel de requêtes atteint';
export const MESSAGE_QUOTA_OFFRE =
	'Le quota mensuel de requêtes de votre offre est atteint ; il se renouvelle le mois prochain.';
export const MESSAGE_QUOTA_CLE_PARTAGEE =
	'Le quota mensuel de requêtes de la clé publique partagée est atteint ; il se renouvelle le mois prochain.';
export const TITRE_ACCES_REFUSE = 'Accès aux données refusé';
const MESSAGE_ACCES_REFUSE_COMPTE =
	"L'accès aux données a été refusé. Rechargez la page ; si vous êtes connecté, reconnectez-vous.";
// Without accounts there is nothing to reconnect to: on the public site a 401/403 comes from the shared
// key, typically while it is being rotated, so the visitor reloads a little later (UI-025).
export const MESSAGE_ACCES_REFUSE = ACCOUNTS_ENABLED
	? MESSAGE_ACCES_REFUSE_COMPTE
	: "L'accès aux données a été refusé. Rechargez la page dans quelques minutes.";
export const TITRE_DEMANDE_REFUSEE = 'Demande refusée';

export function messageDemandeRefusee(statut: number): string {
	return `La demande n'a pas pu être traitée (erreur ${statut}).`;
}

/** Served delay of `Retry-After`, in words: seconds under a minute, whole minutes above. */
function delai(secondes: number): string {
	if (secondes < 60) return `${secondes} ${secondes === 1 ? 'seconde' : 'secondes'}`;
	const minutes = Math.ceil(secondes / 60);
	return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
}

/** A rate limit: the served delay of `Retry-After` when there is one, else a short wait. */
export function messageLimite(retryAfter: number | null): string {
	const attente = retryAfter != null && retryAfter > 0 ? `dans ${delai(retryAfter)}` : 'dans quelques instants';
	return `La limite de requêtes est atteinte. Réessayez ${attente}.`;
}

/** The server names the monthly quota in its problem title (« Quota mensuel epuise »), accented or not. */
function quotaMensuel(err: ApiError): boolean {
	const titre = (err.problemTitle ?? '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
	return titre.startsWith('quota mensuel');
}

export function detailPanne(err: unknown): DetailPanne {
	if (!(err instanceof ApiError)) return { statut: null, titre: null, message: null };
	if (err.status === 429) {
		if (quotaMensuel(err)) {
			return { statut: 429, titre: TITRE_QUOTA, message: err.anonyme ? MESSAGE_QUOTA_CLE_PARTAGEE : MESSAGE_QUOTA_OFFRE };
		}
		return { statut: 429, titre: TITRE_LIMITE, message: messageLimite(err.retryAfter) };
	}
	if (err.status === 401 || err.status === 403) {
		return { statut: err.status, titre: TITRE_ACCES_REFUSE, message: MESSAGE_ACCES_REFUSE };
	}
	if (err.status >= 400 && err.status < 500) {
		return { statut: err.status, titre: TITRE_DEMANDE_REFUSEE, message: messageDemandeRefusee(err.status) };
	}
	return { statut: err.status, titre: null, message: null };
}
