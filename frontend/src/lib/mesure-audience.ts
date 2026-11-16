// Audience measurement (04.7-14b, audit A9): a self-hosted GoatCounter, reached on the site's own
// origin at `/count` (routed by Caddy, which strips the visitor's address, referrer and cookies).
// No third-party script, no cookie, no storage: one GET per page shown, carrying only a path built
// from the ROUTE the page matched, never from the URL: an encoded or odd URL that reaches a page is
// counted as that page's route (Codex 14b n° 1). A dynamic segment is dropped, except the INSEE code
// of a commune fiche, so an address or a DPE document is one path; an error page is counted by its
// kind; the account space is never counted, even where the public build answers it with a 404.
export const POINT_DE_COLLECTE = '/count';

/** INSEE code shape (five digits, or 2A/2B and three digits): the only dynamic segment kept. */
const CODE_INSEE = /^(\d{5}|2[AB]\d{3})$/;

const GROUPES_NON_COMPTES = /^\/\((app|auth)\)(\/|$)/;
const COMPTES = /^\/(admin|settings|billing|mon-espace|login|register|forgot-password|reset-password|verify)(\/|$)/;

export interface PageVue {
	/** Route id of SvelteKit (`/(marketing)/adresse/[id]`); null when no route matched (404). */
	id: string | null;
	params: Record<string, string>;
	/** Path as requested, used only to recognise the account space (never sent). */
	pathname: string;
	statut: number;
}

function decode(pathname: string): string {
	try {
		return decodeURIComponent(pathname);
	} catch {
		return pathname;
	}
}

/** Path to count for *page*; null when it is not counted. */
export function cheminMesure({ id, params, pathname, statut }: PageVue): string | null {
	if ((id && GROUPES_NON_COMPTES.test(id)) || COMPTES.test(decode(pathname))) return null;
	if (statut >= 400 || id === null) return statut === 404 || id === null ? '/erreur-404' : '/erreur';
	const segments: string[] = [];
	for (const segment of id.split('/').filter(Boolean)) {
		if (/^\(.+\)$/.test(segment)) continue;
		const dynamique = /^\[(.+)\]$/.exec(segment);
		if (!dynamique) {
			segments.push(segment);
			continue;
		}
		if (dynamique[1] === 'code' && CODE_INSEE.test(params.code ?? '')) segments.push(params.code);
		break;
	}
	return `/${segments.join('/')}`;
}

// Provenance by channel (owner's decision of 2026-11-11): the referrer is never read. A link the
// publisher spreads himself carries `?src=<canal>`; the entry page of the visit then counts one more
// virtual page, `/venu-de/<canal>`. The list is closed and was agreed name by name: adding a channel
// is a change of this list, of its test, and a new deployment.
export const PARAMETRE_CANAL = 'src';
export const CANAUX = ['reddit', 'linkedin', 'mail', 'presse', 'forum', 'mastodon', 'bluesky', 'datagouv', 'linuxfr', 'hn', 'papier'] as const;
export type Canal = (typeof CANAUX)[number];

/**
 * Channel named by *url*, or null: exactly one `src` parameter whose value is, to the letter, a name
 * of the list. What is returned is the name of the list, never the value of the URL: nothing typed
 * by a visitor can reach the count.
 */
export function canalProvenance(url: URL): Canal | null {
	const valeurs = url.searchParams.getAll(PARAMETRE_CANAL);
	if (valeurs.length !== 1) return null;
	return CANAUX.find((canal) => canal === valeurs[0]) ?? null;
}

/** Virtual page counted for an arrival by *canal*. */
export function cheminProvenance(canal: Canal): string {
	return `/venu-de/${canal}`;
}

/**
 * Query of *url* without the channel parameter, for a page whose canonical address keeps its query:
 * the channel is no part of the address of a page. Without that parameter the query is returned as
 * written (no re-encoding).
 */
export function rechercheSansCanal(url: URL): string {
	if (!url.searchParams.has(PARAMETRE_CANAL)) return url.search;
	const parametres = new URLSearchParams(url.search);
	parametres.delete(PARAMETRE_CANAL);
	const recherche = parametres.toString();
	return recherche ? `?${recherche}` : '';
}

/** The counting request: the aggregated path only (no title, referrer, screen nor query). */
export function urlComptage(chemin: string): string {
	return `${POINT_DE_COLLECTE}?p=${encodeURIComponent(chemin)}`;
}

/**
 * Sends one count; a failed collection is silent and changes nothing on the page. A browser driven
 * by WebDriver sends nothing: flagged as a bot, its hit would be kept by GoatCounter with its full
 * User-Agent, whatever the collection settings (Codex 14b n° 3).
 */
export async function compter(chemin: string): Promise<void> {
	if (typeof navigator !== 'undefined' && navigator.webdriver === true) return;
	try {
		await fetch(urlComptage(chemin), {
			method: 'GET',
			credentials: 'omit',
			referrerPolicy: 'no-referrer',
			cache: 'no-store',
			keepalive: true
		});
	} catch {
		// The measurement is never worth an error shown to the visitor.
	}
}
