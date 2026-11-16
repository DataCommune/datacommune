import { browser } from '$app/environment';
import { untrack } from 'svelte';

// Recently viewed fiches — client-only history persisted in localStorage (D-UX-02, T3.3).
// Zero backend: the hub « Reprendre » section reads this to let a signed-in user resume the
// last fiches they opened. Runes class ($state), guarded by `browser` so SSR/prerender never
// touches window. Corrupt JSON resets silently.

// `bien` is a legacy kind (04-08, D-101): no page records it any more, but entries already stored
// in a browser keep their `/bien/{token}` link, which the 308 of that route sends to the address
// page or to the search.
export type RecentFicheKind = 'commune' | 'bien';

export interface RecentFiche {
	kind: RecentFicheKind;
	// Route id: INSEE code for a commune (/commune/{id}), the legacy token of a bien (/bien/{id}).
	id: string;
	label: string;
	// Epoch ms of the last view — most recent first.
	ts: number;
}

const STORAGE_KEY = 'dc:recent-fiches';
const MAX_ENTRIES = 8;

function isRecentFiche(v: unknown): v is RecentFiche {
	if (typeof v !== 'object' || v === null) return false;
	const e = v as Record<string, unknown>;
	return (
		(e.kind === 'commune' || e.kind === 'bien') &&
		typeof e.id === 'string' &&
		typeof e.label === 'string' &&
		typeof e.ts === 'number'
	);
}

class RecentFichesStore {
	#items = $state<RecentFiche[]>([]);

	constructor() {
		this.#load();
	}

	#load() {
		if (!browser) return;
		try {
			const raw = localStorage.getItem(STORAGE_KEY);
			if (!raw) return;
			const parsed: unknown = JSON.parse(raw);
			this.#items = Array.isArray(parsed)
				? parsed.filter(isRecentFiche).slice(0, MAX_ENTRIES)
				: [];
		} catch {
			// Corrupt JSON → reset silently.
			this.#items = [];
			try {
				localStorage.removeItem(STORAGE_KEY);
			} catch {
				/* storage unavailable — keep the in-memory reset */
			}
		}
	}

	#persist() {
		if (!browser) return;
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(this.#items));
		} catch {
			/* quota / disabled storage — keep the in-memory copy */
		}
	}

	// Record a viewed fiche. Dedupe by (kind,id): a repeat view moves the entry back to the
	// top with a fresh timestamp rather than duplicating it. Caps at MAX_ENTRIES.
	record(entry: { kind: RecentFicheKind; id: string; label: string }) {
		if (!browser) return;
		const next: RecentFiche = { kind: entry.kind, id: entry.id, label: entry.label, ts: Date.now() };
		// The fiche pages call record() from inside a $effect. Reading #items here would
		// register the calling effect as a dependency of the very state we write below,
		// self-triggering an infinite loop (effect_update_depth_exceeded) that breaks the
		// whole reactive root — nav clicks then update the URL but never swap the page.
		// untrack keeps record() a pure side-effect; writes still notify the hub consumers.
		untrack(() => {
			this.#items = [
				next,
				...this.#items.filter((e) => !(e.kind === entry.kind && e.id === entry.id))
			].slice(0, MAX_ENTRIES);
			this.#persist();
		});
	}

	// Most-recent-first snapshot for consumers (e.g. the hub « Reprendre » section).
	list(): RecentFiche[] {
		return this.#items;
	}

	clear() {
		this.#items = [];
		if (!browser) return;
		try {
			localStorage.removeItem(STORAGE_KEY);
		} catch {
			/* storage unavailable — the in-memory list is already cleared */
		}
	}
}

export const recentFiches = new RecentFichesStore();
