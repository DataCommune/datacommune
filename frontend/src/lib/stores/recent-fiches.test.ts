import { describe, it, expect, beforeEach, vi } from 'vitest';

// Force the `browser` guard on so the store touches jsdom's localStorage.
vi.mock('$app/environment', () => ({ browser: true }));

const STORAGE_KEY = 'dc:recent-fiches';

// Re-import the module so its singleton constructor re-runs #load() against the
// current localStorage state (exercises the persistence + corrupt-reset paths).
async function freshStore() {
	vi.resetModules();
	const mod = await import('./recent-fiches.svelte');
	return mod.recentFiches;
}

beforeEach(() => {
	localStorage.clear();
	vi.resetModules();
});

describe('recentFiches store', () => {
	it('records a fiche and lists it', async () => {
		const store = await freshStore();
		store.record({ kind: 'commune', id: '85047', label: 'Challans' });
		expect(store.list().map((e) => e.id)).toEqual(['85047']);
		expect(store.list()[0]).toMatchObject({ kind: 'commune', id: '85047', label: 'Challans' });
		expect(typeof store.list()[0].ts).toBe('number');
	});

	it('keeps the most recently recorded fiche first', async () => {
		const store = await freshStore();
		store.record({ kind: 'commune', id: '85047', label: 'Challans' });
		store.record({ kind: 'bien', id: '47.2,-1.5', label: '1 rue de la Paix' });
		expect(store.list().map((e) => e.id)).toEqual(['47.2,-1.5', '85047']);
	});

	it('dedupes by (kind,id), moving the repeat view to the top with fresh data', async () => {
		const store = await freshStore();
		store.record({ kind: 'commune', id: '85047', label: 'Challans' });
		store.record({ kind: 'commune', id: '44109', label: 'Nantes' });
		store.record({ kind: 'commune', id: '85047', label: 'Challans (à jour)' });
		expect(store.list().map((e) => e.id)).toEqual(['85047', '44109']);
		expect(store.list()[0].label).toBe('Challans (à jour)');
	});

	it('treats the same id across kinds as distinct entries', async () => {
		const store = await freshStore();
		store.record({ kind: 'commune', id: '85047', label: 'Commune' });
		store.record({ kind: 'bien', id: '85047', label: 'Bien' });
		expect(store.list()).toHaveLength(2);
	});

	it('caps the history at 8 entries', async () => {
		const store = await freshStore();
		for (let i = 0; i < 12; i++) store.record({ kind: 'commune', id: String(i), label: 'c' + i });
		const ids = store.list().map((e) => e.id);
		expect(ids).toHaveLength(8);
		expect(ids[0]).toBe('11'); // most recent
		expect(ids.at(-1)).toBe('4'); // oldest still kept
	});

	it('persists to localStorage and reloads on a fresh instance', async () => {
		let store = await freshStore();
		store.record({ kind: 'commune', id: '85047', label: 'Challans' });
		const raw = localStorage.getItem(STORAGE_KEY);
		expect(raw).toBeTruthy();
		expect(JSON.parse(raw!)[0].id).toBe('85047');

		store = await freshStore();
		expect(store.list().map((e) => e.id)).toEqual(['85047']);
	});

	it('resets silently on corrupt JSON', async () => {
		localStorage.setItem(STORAGE_KEY, '{not valid json');
		const store = await freshStore();
		expect(store.list()).toEqual([]);
		expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
	});

	it('drops malformed entries when loading', async () => {
		localStorage.setItem(
			STORAGE_KEY,
			JSON.stringify([
				{ kind: 'commune', id: '85047', label: 'OK', ts: 1 },
				{ bogus: true },
				{ kind: 'invalid', id: '1', label: 'x', ts: 2 }
			])
		);
		const store = await freshStore();
		expect(store.list().map((e) => e.id)).toEqual(['85047']);
	});

	it('clear() empties both the store and localStorage', async () => {
		const store = await freshStore();
		store.record({ kind: 'commune', id: '85047', label: 'Challans' });
		store.clear();
		expect(store.list()).toEqual([]);
		expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
	});
});
