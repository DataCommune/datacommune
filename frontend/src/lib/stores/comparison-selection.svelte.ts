import { browser } from '$app/environment';
import { untrack } from 'svelte';
import { BAN_ID_RE } from '$lib/api/adresses';
import { NUMERO_DPE_RE } from '$lib/api/documents-dpe';
import type { NatureSelection } from '$lib/data/commune-comparison-selection';

// Communes set aside for the comparator (UI-SPEC « /comparer », D-49): 0 to 4 INSEE codes, in
// order, kept in the storage of the tab (`sessionStorage`), browser only. The header counter and
// the « Ajouter à la comparaison » action of the search results read and write this one store.
//
// Codes are checked for the INSEE shape only (5 characters, `2A`/`2B` for Corsica) and are never
// filtered by departement: which communes this instance hosts is read in the API, not here
// (D-34). The stored value can be edited by the visitor: an invalid, duplicated or surplus code
// is dropped when read back, and unreadable JSON is ignored (T-03-04-05).

export const MAX_COMPARAISON = 4;

const STORAGE_KEY = 'dc:comparaison';
const INSEE = /^(?:\d{5}|2[AB]\d{3})$/;
// 04-07: each nature keeps its own selection (UI-SPEC « /comparer »); the communes keep their key.
const STORAGE_KEY_ADRESSES = 'dc:comparaison:adresses';
// 04-10: the DPE documents, and the nature whose selection was modified last (UI-SPEC Q3): the
// header counter counts it, and `/comparer` without parameter opens it.
const STORAGE_KEY_DOCUMENTS = 'dc:comparaison:dpe';
const STORAGE_KEY_DERNIERE_NATURE = 'dc:comparaison:derniere-nature';
const NATURES_CONNUES: readonly string[] = ['commune', 'adresse', 'dpe'];

/** The nature whose selection was modified last in this tab; `null` before any modification. */
class DerniereNature {
	#nature = $state<NatureSelection | null>(null);

	constructor() {
		if (!browser) return;
		try {
			const lue = sessionStorage.getItem(STORAGE_KEY_DERNIERE_NATURE);
			this.#nature = lue !== null && NATURES_CONNUES.includes(lue) ? (lue as NatureSelection) : null;
		} catch {
			this.#nature = null;
		}
	}

	get nature(): NatureSelection | null {
		return this.#nature;
	}

	noter(nature: NatureSelection) {
		this.#nature = nature;
		if (!browser) return;
		try {
			sessionStorage.setItem(STORAGE_KEY_DERNIERE_NATURE, nature);
		} catch {
			/* storage disabled or full: the in-memory nature still serves this page */
		}
	}
}

export const derniereNature = new DerniereNature();

/** What a store keeps: its storage key, the shape of an entry, its link to the comparator. */
interface Nature {
	nom: NatureSelection;
	cle: string;
	normaliser: (code: string) => string;
	valide: (code: string) => boolean;
	lien: (codes: readonly string[]) => string;
}

const COMMUNES: Nature = {
	nom: 'commune',
	cle: STORAGE_KEY,
	normaliser: (code) => code.trim().toUpperCase(),
	valide: (code) => INSEE.test(code),
	lien: (codes) => `/comparer?communes=${codes.join(',')}`
};

const ADRESSES: Nature = {
	nom: 'adresse',
	cle: STORAGE_KEY_ADRESSES,
	normaliser: (id) => id.trim(),
	valide: (id) => BAN_ID_RE.test(id),
	lien: (ids) => `/comparer?adresses=${ids.map(encodeURIComponent).join(',')}`
};

const DOCUMENTS: Nature = {
	nom: 'dpe',
	cle: STORAGE_KEY_DOCUMENTS,
	normaliser: (numero) => numero.trim(),
	valide: (numero) => NUMERO_DPE_RE.test(numero),
	lien: (numeros) => `/comparer?dpe=${numeros.map(encodeURIComponent).join(',')}`
};

export type AjoutComparaison =
	| { ajoute: true; rang: number }
	| { ajoute: false; raison: 'limite' | 'doublon' | 'invalide' };

/** In-place confirmation of « Ajouter à la comparaison » (search results, directory). */
export type ConfirmationAjout = { texte: string; lien: boolean } | null;

/** The sentence a commune card confirms an addition with; `count` is the selection after it. */
export function confirmationAjout(ajout: AjoutComparaison, count: number): ConfirmationAjout {
	const rang = `(${count}/${MAX_COMPARAISON})`;
	if (ajout.ajoute) return { texte: `Ajoutée à la comparaison ${rang}`, lien: true };
	if (ajout.raison === 'doublon') return { texte: `Déjà dans la comparaison ${rang}`, lien: true };
	if (ajout.raison === 'limite') {
		return { texte: 'Quatre communes au maximum : retirez-en une pour en ajouter une autre.', lien: false };
	}
	return null;
}

/** Valid entries of a nature, first occurrence kept, at most four: the only shape ever stored. */
function sanitize(nature: Nature, values: readonly unknown[]): string[] {
	const codes: string[] = [];
	for (const value of values) {
		if (typeof value !== 'string') continue;
		const code = nature.normaliser(value);
		if (nature.valide(code) && !codes.includes(code)) codes.push(code);
		if (codes.length === MAX_COMPARAISON) break;
	}
	return codes;
}

class ComparisonSelectionStore {
	#codes = $state<string[]>([]);
	readonly #nature: Nature;

	constructor(nature: Nature) {
		this.#nature = nature;
		this.#load();
	}

	#load() {
		if (!browser) return;
		try {
			const raw = sessionStorage.getItem(this.#nature.cle);
			if (!raw) return;
			const parsed: unknown = JSON.parse(raw);
			this.#codes = Array.isArray(parsed) ? sanitize(this.#nature, parsed) : [];
		} catch {
			this.#codes = [];
		}
	}

	#write(codes: string[]) {
		// Only a real change is a modification: a link read again with the same objects is not.
		const modifiee = codes.length !== this.#codes.length || codes.some((code, index) => code !== this.#codes[index]);
		this.#codes = codes;
		if (modifiee) derniereNature.noter(this.#nature.nom);
		if (!browser) return;
		try {
			sessionStorage.setItem(this.#nature.cle, JSON.stringify(codes));
		} catch {
			/* storage disabled or full: the in-memory selection still serves this page */
		}
	}

	/** Selected codes, in comparison order. */
	get codes(): readonly string[] {
		return this.#codes;
	}

	get count(): number {
		return this.#codes.length;
	}

	has(code: string): boolean {
		return this.#codes.includes(this.#nature.normaliser(code));
	}

	/** Link to the comparator with the current selection. */
	get href(): string {
		return this.#codes.length ? this.#nature.lien(this.#codes) : '/comparer';
	}

	add(code: string): AjoutComparaison {
		const normalized = this.#nature.normaliser(code);
		// untrack: a caller running inside an effect must not subscribe to what it writes.
		return untrack(() => {
			if (!this.#nature.valide(normalized)) return { ajoute: false, raison: 'invalide' } as const;
			if (this.#codes.includes(normalized)) return { ajoute: false, raison: 'doublon' } as const;
			if (this.#codes.length >= MAX_COMPARAISON) return { ajoute: false, raison: 'limite' } as const;
			this.#write([...this.#codes, normalized]);
			return { ajoute: true, rang: this.#codes.length } as const;
		});
	}

	remove(code: string) {
		const normalized = this.#nature.normaliser(code);
		untrack(() => this.#write(this.#codes.filter((current) => current !== normalized)));
	}

	moveFirst(code: string) {
		const normalized = this.#nature.normaliser(code);
		untrack(() => {
			if (!this.#codes.includes(normalized)) return;
			this.#write([normalized, ...this.#codes.filter((current) => current !== normalized)]);
		});
	}

	/** Replaces the whole selection (e.g. from a shared link), sanitized like a stored value. */
	replace(codes: readonly string[]) {
		untrack(() => this.#write(sanitize(this.#nature, codes)));
	}
}

export const comparisonSelection = new ComparisonSelectionStore(COMMUNES);

/** Addresses set aside for the comparator (04-07), kept apart from the communes. */
export const addressComparisonSelection = new ComparisonSelectionStore(ADRESSES);

/** DPE documents set aside for the comparator (04-10), kept apart from the other natures. */
export const documentComparisonSelection = new ComparisonSelectionStore(DOCUMENTS);

const SELECTIONS: Readonly<Record<NatureSelection, ComparisonSelectionStore>> = {
	commune: comparisonSelection,
	adresse: addressComparisonSelection,
	dpe: documentComparisonSelection
};

/** The selection of one nature. */
export function selectionDeNature(nature: NatureSelection): ComparisonSelectionStore {
	return SELECTIONS[nature];
}

/** The selection the header counts (UI-SPEC Q3): the nature modified last, else the communes. */
export function selectionDerniereNature(): ComparisonSelectionStore {
	return SELECTIONS[derniereNature.nature ?? 'commune'];
}
