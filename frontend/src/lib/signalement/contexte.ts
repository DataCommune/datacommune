// Context of an error report: what the information panel puts in its link, what the page reads back
// on the server, and the check of the two typed fields. Everything read from the address bar or from
// the form is bounded here, with the bounds of the API (`app/signalement/schemas.py`): the page
// truncates a context that is too long rather than lose the report.

export const BORNES = {
	commentaireMin: 10,
	commentaireMax: 2000,
	email: 254,
	page: 300,
	titre: 200,
	lignes: 600,
	source: 200,
	version: 64,
	userAgent: 400,
	piege: 200,
	largeur: 20_000
} as const;

export const CHEMIN_SIGNALER = '/signaler';

export interface ContexteSignalement {
	/** Path of the page reported, never another origin. */
	page: string | null;
	/** Title of the information panel the report was opened from. */
	titre: string | null;
	/** Served fields and their raw values, `field=value ; field=value`. */
	lignes: string | null;
	source: string | null;
	/** Deployment SHA: the one of the visitor's bundle, else the server's. */
	version: string;
}

const CHEMIN = /^\/(?!\/)[^\s\\]*$/;
const VERSION = /^[0-9A-Za-z._-]+$/;
// Deliberately loose: the API validates the address; this only catches a typo before the round trip.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** *valeur* trimmed and cut to *max* characters; null when nothing is left. */
export function borne(valeur: string | null | undefined, max: number): string | null {
	const texte = (valeur ?? '').trim().slice(0, max).trim();
	return texte === '' ? null : texte;
}

/** The path of a page of the site, without query string or fragment; null for anything else. */
export function cheminDuSite(valeur: string | null | undefined): string | null {
	const chemin = (valeur ?? '').trim().split(/[?#]/, 1)[0];
	return chemin.length <= BORNES.page && CHEMIN.test(chemin) ? chemin : null;
}

function version(valeur: string | null | undefined): string | null {
	const texte = (valeur ?? '').trim();
	return texte.length <= BORNES.version && VERSION.test(texte) ? texte : null;
}

/** Link of the information panel to the form, carrying what the panel knows of the value. */
export function lienSignalement(volet: {
	page: string;
	titre: string;
	brut: readonly { fieldId: string; valeur: string }[];
	source?: string | null;
	version: string;
}): string {
	const params = new URLSearchParams();
	const ajouter = (nom: string, valeur: string | null) => {
		if (valeur) params.set(nom, valeur);
	};
	ajouter('de', cheminDuSite(volet.page));
	ajouter('titre', borne(volet.titre, BORNES.titre));
	ajouter('lignes', borne(volet.brut.map((servie) => `${servie.fieldId}=${servie.valeur}`).join(' ; '), BORNES.lignes));
	ajouter('source', borne(volet.source, BORNES.source));
	ajouter('v', version(volet.version));
	return `${CHEMIN_SIGNALER}?${params}`;
}

/** The page the visitor comes from, when it is a page of this site other than the form. */
function pageDeProvenance(referer: string | null, origine: string): string | null {
	if (!referer) return null;
	let url: URL;
	try {
		url = new URL(referer);
	} catch {
		return null;
	}
	if (url.origin !== origine || url.pathname === CHEMIN_SIGNALER) return null;
	return cheminDuSite(url.pathname);
}

/** Context of the form: the link of the panel, else the page left behind (works without JavaScript). */
export function lireContexte(url: URL, referer: string | null, versionServeur: string): ContexteSignalement {
	const q = url.searchParams;
	return {
		page: q.has('de') ? cheminDuSite(q.get('de')) : pageDeProvenance(referer, url.origin),
		titre: borne(q.get('titre'), BORNES.titre),
		lignes: borne(q.get('lignes'), BORNES.lignes),
		source: borne(q.get('source'), BORNES.source),
		version: version(q.get('v')) ?? versionServeur
	};
}

/** Context sent back by the form itself (hidden fields): bounded like the address bar. */
export function contexteDuFormulaire(lire: (nom: string) => string, versionServeur: string): ContexteSignalement {
	return {
		page: cheminDuSite(lire('page')),
		titre: borne(lire('titre'), BORNES.titre),
		lignes: borne(lire('lignes'), BORNES.lignes),
		source: borne(lire('source'), BORNES.source),
		version: version(lire('version')) ?? versionServeur
	};
}

export interface ErreursSaisie {
	commentaire?: 'court' | 'long';
	email?: 'invalide';
}

/** What is wrong with the two typed fields; empty when they can be sent. */
export function validerSaisie(commentaire: string, email: string): ErreursSaisie {
	const erreurs: ErreursSaisie = {};
	if (commentaire.length < BORNES.commentaireMin) erreurs.commentaire = 'court';
	else if (commentaire.length > BORNES.commentaireMax) erreurs.commentaire = 'long';
	if (email !== '' && (email.length > BORNES.email || !EMAIL.test(email))) erreurs.email = 'invalide';
	return erreurs;
}
