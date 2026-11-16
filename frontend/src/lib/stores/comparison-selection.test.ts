import { describe, it, expect, beforeEach, vi } from 'vitest';

// Force the `browser` guard on so the store touches jsdom's sessionStorage.
vi.mock('$app/environment', () => ({ browser: true }));

const STORAGE_KEY = 'dc:comparaison';

// Re-import the module so its singleton constructor reads the current tab storage again.
async function freshStore() {
	vi.resetModules();
	return (await import('./comparison-selection.svelte')).comparisonSelection;
}

function stored(): unknown {
	const raw = sessionStorage.getItem(STORAGE_KEY);
	return raw === null ? null : JSON.parse(raw);
}

beforeEach(() => {
	sessionStorage.clear();
	localStorage.clear();
});

describe('comparisonSelection', () => {
	it('keeps the order of addition and reports the rank', async () => {
		const store = await freshStore();

		expect(store.add('79211')).toEqual({ ajoute: true, rang: 1 });
		expect(store.add('2a004')).toEqual({ ajoute: true, rang: 2 });

		expect(store.codes).toEqual(['79211', '2A004']);
		expect(store.count).toBe(2);
		expect(store.href).toBe('/comparer?communes=79211,2A004');
	});

	it('refuses a duplicate without changing the selection', async () => {
		const store = await freshStore();
		store.add('79211');

		expect(store.add('79211')).toEqual({ ajoute: false, raison: 'doublon' });
		expect(store.codes).toEqual(['79211']);
	});

	it('refuses a fifth commune', async () => {
		const store = await freshStore();
		for (const code of ['79211', '35999', '2B033', '97105']) store.add(code);

		expect(store.add('01001')).toEqual({ ajoute: false, raison: 'limite' });
		expect(store.codes).toEqual(['79211', '35999', '2B033', '97105']);
	});

	it('refuses a code that is not an INSEE code, whatever its departement', async () => {
		const store = await freshStore();

		expect(store.add('7921')).toEqual({ ajoute: false, raison: 'invalide' });
		expect(store.add('2C004')).toEqual({ ajoute: false, raison: 'invalide' });
		// Never filtered by a hosted perimeter: any real-shaped code is accepted (D-34).
		expect(store.add('06088')).toEqual({ ajoute: true, rang: 1 });
	});

	it('persists in the tab storage only', async () => {
		const store = await freshStore();
		store.add('79211');
		store.add('35999');

		expect(stored()).toEqual(['79211', '35999']);
		expect(localStorage.length).toBe(0);
	});

	it('reads the tab storage back', async () => {
		sessionStorage.setItem(STORAGE_KEY, JSON.stringify(['79211', '35999']));

		const store = await freshStore();

		expect(store.codes).toEqual(['79211', '35999']);
	});

	it('drops invalid, duplicated and surplus codes of an altered storage', async () => {
		sessionStorage.setItem(
			STORAGE_KEY,
			JSON.stringify(['79211', 'nope', 42, '79211', '2a004', '35999', '01001', '06088'])
		);

		const store = await freshStore();

		expect(store.codes).toEqual(['79211', '2A004', '35999', '01001']);
	});

	it('ignores an unreadable storage', async () => {
		sessionStorage.setItem(STORAGE_KEY, '{not json');

		const store = await freshStore();

		expect(store.codes).toEqual([]);
		expect(store.add('79211')).toEqual({ ajoute: true, rang: 1 });
	});

	it('removes, moves first and replaces', async () => {
		const store = await freshStore();
		for (const code of ['79211', '35999', '2B033']) store.add(code);

		store.moveFirst('2b033');
		expect(store.codes).toEqual(['2B033', '79211', '35999']);

		store.remove('79211');
		expect(store.codes).toEqual(['2B033', '35999']);
		expect(stored()).toEqual(['2B033', '35999']);

		store.replace(['01001', 'bad', '01001', '06088', '13055', '31555', '33063']);
		expect(store.codes).toEqual(['01001', '06088', '13055', '31555']);
		expect(store.href).toBe('/comparer?communes=01001,06088,13055,31555');
	});

	it('links to the empty comparator without a selection', async () => {
		const store = await freshStore();

		expect(store.href).toBe('/comparer');
		expect(store.has('79211')).toBe(false);
	});
});

// --- 04-07: the addresses keep their own selection, the communes their key -----------------------

describe('addressComparisonSelection (04-07)', () => {
	const A = '85191_0420_00012';
	const B = '85191_0601_00001';

	async function freshStores() {
		vi.resetModules();
		const module = await import('./comparison-selection.svelte');
		return { communes: module.comparisonSelection, adresses: module.addressComparisonSelection };
	}

	it('stores the addresses under their own key, never in the communes selection', async () => {
		const { communes, adresses } = await freshStores();
		communes.add('44109');

		expect(adresses.add(A)).toEqual({ ajoute: true, rang: 1 });
		expect(adresses.add(B)).toEqual({ ajoute: true, rang: 2 });
		expect(adresses.add(A)).toEqual({ ajoute: false, raison: 'doublon' });
		expect(adresses.add('44109')).toEqual({ ajoute: false, raison: 'invalide' });
		expect(communes.add(A)).toEqual({ ajoute: false, raison: 'invalide' });

		expect(JSON.parse(sessionStorage.getItem('dc:comparaison:adresses') ?? 'null')).toEqual([A, B]);
		expect(stored()).toEqual(['44109']);
		expect(communes.count).toBe(1);
		expect(adresses.href).toBe(`/comparer?adresses=${A},${B}`);
		expect(communes.href).toBe('/comparer?communes=44109');
	});

	it('reads its storage back, dropping altered entries, and replaces from a link', async () => {
		sessionStorage.setItem('dc:comparaison:adresses', JSON.stringify([A, 'x', A, 12, B]));
		const { adresses } = await freshStores();
		expect(adresses.codes).toEqual([A, B]);
		adresses.moveFirst(B);
		expect(adresses.codes).toEqual([B, A]);
		adresses.replace([A]);
		expect(adresses.codes).toEqual([A]);
		adresses.remove(A);
		expect(adresses.href).toBe('/comparer');
	});
});

// --- 04-10: DPE documents, and the nature modified last (UI-SPEC Q3) -----------------------------

async function freshModule() {
	vi.resetModules();
	return import('./comparison-selection.svelte');
}

describe('documentComparisonSelection and the nature modified last (04-10)', () => {
	it('stores the DPE numbers under their own key, never in another nature', async () => {
		const { documentComparisonSelection, comparisonSelection } = await freshModule();
		expect(documentComparisonSelection.add('2385E0123456A')).toEqual({ ajoute: true, rang: 1 });
		expect(documentComparisonSelection.add('2385e0123456a')).toEqual({ ajoute: false, raison: 'invalide' });
		expect(JSON.parse(sessionStorage.getItem('dc:comparaison:dpe')!)).toEqual(['2385E0123456A']);
		expect(stored()).toBeNull();
		expect(comparisonSelection.count).toBe(0);
		expect(documentComparisonSelection.href).toBe('/comparer?dpe=2385E0123456A');
	});

	it('writes the nature modified last at each modification, never on a read of the same objects', async () => {
		const { derniereNature, selectionDerniereNature, comparisonSelection, addressComparisonSelection, documentComparisonSelection } =
			await freshModule();
		expect(derniereNature.nature).toBeNull();
		comparisonSelection.add('79211');
		expect(derniereNature.nature).toBe('commune');
		documentComparisonSelection.replace(['2385E0123456A', '2185E0000001A']);
		expect(sessionStorage.getItem('dc:comparaison:derniere-nature')).toBe('dpe');
		expect(selectionDerniereNature().count).toBe(2);
		// The same objects read again (a link opened again) are not a modification.
		comparisonSelection.replace(['79211']);
		addressComparisonSelection.replace([]);
		expect(derniereNature.nature).toBe('dpe');
		comparisonSelection.remove('79211');
		expect(derniereNature.nature).toBe('commune');
		expect(selectionDerniereNature().count).toBe(0);
	});

	it('reads the nature modified last back, an altered value ignored (the communes then)', async () => {
		sessionStorage.setItem('dc:comparaison:derniere-nature', 'adresse');
		expect((await freshModule()).derniereNature.nature).toBe('adresse');
		sessionStorage.setItem('dc:comparaison:derniere-nature', 'bien');
		const { derniereNature, selectionDerniereNature, comparisonSelection } = await freshModule();
		expect(derniereNature.nature).toBeNull();
		expect(selectionDerniereNature()).toBe(comparisonSelection);
	});
});
