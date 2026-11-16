// R-03 (2026-11-10): `/communes?q=75000` on the national instance named the 101 departements in one
// sentence. The page now gives their number, and the arrondissement advice for 75000, 13000, 69000.
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { describe, expect, it, vi } from 'vitest';
import { DEPARTEMENTS } from '$lib/data/departements';
import { CONSEIL_CODE_POSTAL_PLM, CONSEIL_RECHERCHE_PAR_NOM } from '$lib/data/recherche-code-postal';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://fixture/communes?q=75000') })
}));

import { load, type CommunesPageData } from './+page';
import Page from './+page.svelte';

const TOUS = DEPARTEMENTS.map((d) => d.code).filter((code) => code !== '975');
const NATIONAL = { etat: 'heberge', motif: null, departements: TOUS };

function json(body: unknown) {
	return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

function api() {
	return vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input), 'http://origin.invalid');
		if (url.pathname === '/v1/adresses/recherche') {
			return json({ statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: NATIONAL } } });
		}
		return json({ statut: 'ok', donnees: [], meta: { hebergement: { ban: NATIONAL } } });
	});
}

async function page(q: string) {
	const url = new URL('https://fixture/communes');
	url.searchParams.set('q', q);
	const data = (await (load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>)({
		url,
		fetch: api()
	})) as CommunesPageData;
	// The notice only: the departement filter lists every departement by name.
	return render(Page, { props: { data } }).container.querySelector('.communes-notice')?.textContent ?? '';
}

describe('/communes?q=<code postal> on the national instance (R-03)', () => {
	it('75000: the number of departements and the arrondissement advice, no list of names', async () => {
		const texte = await page('75000');

		expect(texte).toContain('La recherche par code postal porte sur 101 départements.');
		expect(texte).toContain(CONSEIL_CODE_POSTAL_PLM);
		expect(texte).not.toContain('Ain (01)');
		expect(texte).not.toContain('Ain (01)');
	});

	it('44999: the number, and the name advice', async () => {
		const texte = await page('44999');

		expect(texte).toContain('La recherche par code postal porte sur 101 départements.');
		expect(texte).toContain(CONSEIL_RECHERCHE_PAR_NOM);
		expect(texte).not.toContain(CONSEIL_CODE_POSTAL_PLM);
	});
});
