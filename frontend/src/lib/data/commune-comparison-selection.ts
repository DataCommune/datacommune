// Selection of the comparator read from its link (UI-SPEC « /comparer », D-49): 2 to 4 INSEE codes
// in `communes`, in column order, and the « Différences seulement » filter in `diff=1`. The link is
// shared and editable, so nothing in it is trusted (T-03-10-01):
//   - a code is kept only in the INSEE shape (5 digits, or 2A/2B + 3 digits for Corsica); it is
//     never filtered by departement — which communes this instance hosts is read in the API (D-34);
//   - a malformed code, a duplicate and anything past the fourth commune are dropped, each with
//     the sentence of the contract, so the visitor knows the link was altered;
//   - the codes the referential does not know are reported by the API (`codes_inconnus`) and
//     worded with the same sentence by the page.
// Pure module: no request, no storage. The page keeps the tab storage in step (URL first).

import { BAN_ID_RE } from '$lib/api/adresses';
import { NUMERO_DPE_RE } from '$lib/api/documents-dpe';

/** Most communes the comparator shows side by side (D-49). */
export const MAX_COMMUNES = 4;

const INSEE = /^(?:\d{5}|2[AB]\d{3})$/;

/** Longest altered code quoted back in a message: a forged link cannot fill the page. */
const CITATION_MAX = 16;

export type StatutSelection = 'vide' | 'une' | 'prete';

export interface SelectionComparaison {
	/** `vide` (0 commune), `une` (1: no comparison call), `prete` (2 to 4). */
	statut: StatutSelection;
	codes: string[];
	/** « Différences seulement » (`diff=1`). */
	differences: boolean;
	/** Sentences of the contract about what the link carried and was dropped (« Lien altéré »). */
	messages: string[];
	/** Communes only (04.1-06, D-123): collectivities out of coverage the link carried, said apart. */
	horsCouverture?: string[];
	/** Addresses only (04-11): the designated DPE kept from the link, one per address at most. */
	designations?: Designation[];
	/** Addresses only: pairs of a compared address the link carried in a form that is not kept. */
	designationsEcartees?: Designation[];
}

export const MESSAGE_LIMITE = 'Seules les quatre premières communes du lien sont affichées.';

function citation(code: string): string {
	return code.length > CITATION_MAX ? `${code.slice(0, CITATION_MAX)}…` : code;
}

/** « Lien altéré »: a code the link carried that is not, or is no longer, a known commune. */
export function messageCodeInconnu(code: string): string {
	return `Le lien contenait un code INSEE inconnu (${citation(code)}) ; il a été écarté.`;
}

/** « Hors couverture » (D-123): an overseas collectivity is known, but this site does not cover it. */
export function messageCodeHorsCouverture(code: string): string {
	return `${citation(code)} : collectivité d'outre-mer, hors du périmètre couvert par ce site ; elle n'a pas de colonne.`;
}

export function messageDoublon(code: string): string {
	return `Le lien contenait deux fois la même commune (${code}) ; le doublon a été écarté.`;
}

/** A notice already said is not said again: the same altered entry repeated in a link names one fault. */
function direUneFois(messages: string[], message: string): void {
	if (!messages.includes(message)) messages.push(message);
}

export function statutDe(codes: readonly string[]): StatutSelection {
	return codes.length === 0 ? 'vide' : codes.length === 1 ? 'une' : 'prete';
}

/** Reads `communes` (and `diff`) from a link; every other parameter is ignored. */
export function lireSelection(params: URLSearchParams): SelectionComparaison {
	const codes: string[] = [];
	const messages: string[] = [];
	const entrees = params
		.getAll('communes')
		.flatMap((valeur) => valeur.split(','))
		.map((valeur) => valeur.trim().toUpperCase())
		// An empty entry (trailing comma) carries no code: nothing to report.
		.filter((valeur) => valeur !== '');
	for (const code of entrees) {
		if (codes.length === MAX_COMMUNES) {
			direUneFois(messages, MESSAGE_LIMITE);
			break;
		}
		if (!INSEE.test(code)) direUneFois(messages, messageCodeInconnu(code));
		else if (codes.includes(code)) direUneFois(messages, messageDoublon(code));
		else codes.push(code);
	}
	return { statut: statutDe(codes), codes, differences: params.get('diff') === '1', messages };
}

/** Link of a selection: communes in column order, then the filter. */
export function lienSelection(codes: readonly string[], differences: boolean): string {
	const parametres: string[] = [];
	if (codes.length) parametres.push(`communes=${codes.join(',')}`);
	if (differences) parametres.push('diff=1');
	return parametres.length ? `/comparer?${parametres.join('&')}` : '/comparer';
}

/** The selection with `code` appended; unchanged when full, already there or malformed. */
export function avecCommune(codes: readonly string[], code: string): string[] {
	const normalise = code.trim().toUpperCase();
	if (!INSEE.test(normalise) || codes.includes(normalise) || codes.length >= MAX_COMMUNES) return [...codes];
	return [...codes, normalise];
}

export function sansCommune(codes: readonly string[], code: string): string[] {
	return codes.filter((courant) => courant !== code);
}

/** `code` in the first column: the gaps of every other column are then read against it. */
export function enPremier(codes: readonly string[], code: string): string[] {
	return codes.includes(code) ? [code, ...codes.filter((courant) => courant !== code)] : [...codes];
}

// --- Nature of the comparison (04-07, 04-10, D-102): one nature, never two -------------------
//
// `?communes=` (unchanged), `?adresses=id1,id2&diff=1` or `?dpe=n1,n2&diff=1`. A link carrying two
// natures shows the first one in the order communes → addresses → dpe, with the « Lien altéré »
// sentence of the contract. An address key is kept in the served BAN shape only (`BAN_ID_RE`), a
// DPE number in the ADEME form only (`NUMERO_DPE_RE`); the keys the API does not know are reported
// by it (`ids_ecartes`, `numeros_ecartes`) and worded by the page.


export type NatureSelection = 'commune' | 'adresse' | 'dpe';

/** Order of the natures (UI-SPEC « URL »): the first one of a mixed link is shown. */
export const NATURES: readonly NatureSelection[] = ['commune', 'adresse', 'dpe'];

/** Parameter of each nature in the link. */
export const PARAMETRES_NATURE: Readonly<Record<NatureSelection, string>> = {
	commune: 'communes',
	adresse: 'adresses',
	dpe: 'dpe'
};

export interface SelectionNature extends SelectionComparaison {
	nature: NatureSelection;
}

export const MESSAGE_LIMITE_ADRESSES = 'Seules les quatre premières adresses du lien sont affichées.';

/** « Lien altéré »: an address key the link carried that is not, or no longer, a known address. */
export function messageAdresseInconnue(id: string): string {
	return `Le lien contenait un identifiant d'adresse inconnu (${citation(id)}) ; il a été écarté.`;
}

/** « Lien altéré »: a link that mixed two natures shows the first one only. */
export function messageNaturesMelangees(nature: NatureSelection): string {
	return `Le lien mélangeait plusieurs natures d'objets ; seules les ${nature === 'commune' ? 'communes' : 'adresses'} sont affichées.`;
}

/**
 * UI-SPEC « Lien altéré »: « Seules les quatre premières {adresses / documents DPE} du lien sont
 * affichées. », agreed with its masculine object as the owner decided for the participles (row 46).
 */
export const MESSAGE_LIMITE_DPE = 'Seuls les quatre premiers documents DPE du lien sont affichés.';

/** « Lien altéré »: a DPE number the link carried that is not, or no longer, a known document. */
export function messageDpeInconnu(numero: string): string {
	return `Le lien contenait un numéro DPE inconnu (${citation(numero)}) ; il a été écarté.`;
}

// --- Designation of a DPE on a compared address (04-11, D-102): in the link only -------------------
//
// `designes=<ban_id>:<numero>,…`, one pair per compared address at most, the pairs in column order.
// A designation lives in the link only: never in the tab storage, never in a database. A pair whose
// address is not compared is dropped silently (it names nothing on the page); a pair of a compared
// address whose number is not of the ADEME form, or a second pair for the same address, is set
// aside and said with the sentence of the contract once the address is named. The API keeps a
// pair only when the number is declared at this address (`designations_ecartees`).

/** A DPE designated on a compared address. */
export interface Designation {
	ban_id: string;
	numero: string;
}

/** UI-SPEC « Lien altéré (ajouts) »: a designated number not served for this address. */
export function messageDesignationRetiree(adresse: string): string {
	return `Le DPE désigné pour ${adresse} n'est pas déclaré à cette adresse ; la désignation a été retirée.`;
}

/** Reads `designes` for the compared addresses `adresses` (validated keys, column order). */
export function lireDesignations(
	params: URLSearchParams,
	adresses: readonly string[]
): { designations: Designation[]; ecartees: Designation[] } {
	const designations: Designation[] = [];
	const ecartees: Designation[] = [];
	const paires = params
		.getAll('designes')
		.flatMap((valeur) => valeur.split(','))
		.map((valeur) => valeur.trim())
		.filter((valeur) => valeur !== '');
	for (const paire of paires) {
		const separateur = paire.lastIndexOf(':');
		if (separateur <= 0) continue;
		const lue = { ban_id: paire.slice(0, separateur), numero: paire.slice(separateur + 1) };
		if (!adresses.includes(lue.ban_id)) continue;
		const deja = designations.find((designation) => designation.ban_id === lue.ban_id);
		if (deja && deja.numero === lue.numero) continue;
		if (deja || !NUMERO_DPE_RE.test(lue.numero)) ecartees.push(lue);
		else designations.push(lue);
	}
	// Column order: the link written back is stable whatever the order it was typed in.
	designations.sort((a, b) => adresses.indexOf(a.ban_id) - adresses.indexOf(b.ban_id));
	return { designations, ecartees };
}

/** The designations with `numero` designated on `banId` (replacing its former one). */
export function avecDesignation(designations: readonly Designation[], banId: string, numero: string): Designation[] {
	if (!NUMERO_DPE_RE.test(numero)) return [...designations];
	return [...designations.filter((designation) => designation.ban_id !== banId), { ban_id: banId, numero }];
}

/** The designations without the one of `banId`. */
export function sansDesignation(designations: readonly Designation[], banId: string): Designation[] {
	return designations.filter((designation) => designation.ban_id !== banId);
}

/** Reads `adresses` (and `diff`); a duplicate is dropped (the API would drop it too). */
export function lireSelectionAdresses(params: URLSearchParams): SelectionComparaison {
	const codes: string[] = [];
	const messages: string[] = [];
	const entrees = params
		.getAll('adresses')
		.flatMap((valeur) => valeur.split(','))
		.map((valeur) => valeur.trim())
		.filter((valeur) => valeur !== '');
	for (const id of entrees) {
		if (codes.length === MAX_COMMUNES) {
			direUneFois(messages, MESSAGE_LIMITE_ADRESSES);
			break;
		}
		if (!BAN_ID_RE.test(id)) direUneFois(messages, messageAdresseInconnue(id));
		else if (!codes.includes(id)) codes.push(id);
	}
	const { designations, ecartees } = lireDesignations(params, codes);
	return {
		statut: statutDe(codes),
		codes,
		differences: params.get('diff') === '1',
		messages,
		// Present only when the link carries designations: a link without any reads as before 04-11.
		...(designations.length ? { designations } : {}),
		...(ecartees.length ? { designationsEcartees: ecartees } : {})
	};
}

/** Reads `dpe` (and `diff`); a duplicate is dropped (the API would drop it too). */
export function lireSelectionDocuments(params: URLSearchParams): SelectionComparaison {
	const codes: string[] = [];
	const messages: string[] = [];
	const entrees = params
		.getAll('dpe')
		.flatMap((valeur) => valeur.split(','))
		.map((valeur) => valeur.trim())
		.filter((valeur) => valeur !== '');
	for (const numero of entrees) {
		if (codes.length === MAX_COMMUNES) {
			direUneFois(messages, MESSAGE_LIMITE_DPE);
			break;
		}
		if (!NUMERO_DPE_RE.test(numero)) direUneFois(messages, messageDpeInconnu(numero));
		else if (!codes.includes(numero)) codes.push(numero);
	}
	return { statut: statutDe(codes), codes, differences: params.get('diff') === '1', messages };
}

/** The nature of a link and its selection: communes, then addresses, then documents (UI-SPEC « URL »). */
export function lireSelectionNature(params: URLSearchParams): SelectionNature {
	const presentes = NATURES.filter((nature) => params.has(PARAMETRES_NATURE[nature]));
	const nature = presentes[0] ?? 'commune';
	const selection =
		nature === 'adresse' ? lireSelectionAdresses(params) : nature === 'dpe' ? lireSelectionDocuments(params) : lireSelection(params);
	if (presentes.length > 1) selection.messages.push(messageNaturesMelangees(nature));
	return { nature, ...selection };
}

/**
 * Link of a selection of one nature: objects in column order, the designations of the addresses
 * still compared (04-11, in column order), then the filter.
 */
export function lienSelectionNature(
	nature: NatureSelection,
	codes: readonly string[],
	differences: boolean,
	designations: readonly Designation[] = []
): string {
	if (nature === 'commune') return lienSelection(codes, differences);
	const parametres: string[] = [];
	if (codes.length) parametres.push(`${PARAMETRES_NATURE[nature]}=${codes.map(encodeURIComponent).join(',')}`);
	if (nature === 'adresse') {
		const gardees = codes.flatMap((code) => designations.filter((designation) => designation.ban_id === code));
		if (gardees.length) {
			parametres.push(`designes=${gardees.map((designation) => `${encodeURIComponent(designation.ban_id)}:${designation.numero}`).join(',')}`);
		}
	}
	if (differences) parametres.push('diff=1');
	return parametres.length ? `/comparer?${parametres.join('&')}` : '/comparer';
}

/**
 * Link of a nature in the nature switch (`NatureNav`): its selection, or the empty comparator of
 * that nature (`?adresses=`, `?dpe=`), which a link without parameter would open as communes.
 */
export function lienNature(nature: NatureSelection, codes: readonly string[]): string {
	if (codes.length || nature === 'commune') return lienSelectionNature(nature, codes, false);
	return `/comparer?${PARAMETRES_NATURE[nature]}=`;
}

/** The selection with the DPE number appended; unchanged when full, already there or malformed. */
export function avecDocument(numeros: readonly string[], numero: string): string[] {
	const cle = numero.trim();
	if (!NUMERO_DPE_RE.test(cle) || numeros.includes(cle) || numeros.length >= MAX_COMMUNES) return [...numeros];
	return [...numeros, cle];
}

/** The selection with the address `id` appended; unchanged when full, already there or malformed. */
export function avecAdresse(ids: readonly string[], id: string): string[] {
	const cle = id.trim();
	if (!BAN_ID_RE.test(cle) || ids.includes(cle) || ids.length >= MAX_COMMUNES) return [...ids];
	return [...ids, cle];
}
