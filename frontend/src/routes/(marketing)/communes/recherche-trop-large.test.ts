// R-08 (2026-11-10): on `/communes?q=`, an address search served 200 `etat: 'trop_large'` is its
// own state of the address group: neither the outage (« momentanément indisponible », « Relancer »,
// the same read again) nor « Aucune … adresse » (nothing was read to the end). A real 503 keeps the
// outage (témoin).
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { describe, expect, it, vi } from 'vitest';
import type { CommuneSearchResult } from '$lib/api/communes';
import { RECHERCHE_TROP_LARGE } from '$lib/data/recherche-lieux';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://fixture/communes?q=equeurdreville') })
}));

import { load, type CommunesPageData } from './+page';
import Page from './+page.svelte';

const LE_PIN: CommuneSearchResult = {
	code_insee: '35999',
	nom: 'Le Pin',
	code_postal: '35170',
	departement: '35',
	population: 750,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: { etat: 'heberge', motif: null, departements: ['35'] }
};
const HEBERGE = { etat: 'heberge' as const, motif: null, departements: ['85'] };

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const tropLarge = () =>
	json({
		statut: 'ok',
		donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: HEBERGE }, correspondance_numero: null, etat: 'trop_large' }
	});

function api(communes: CommuneSearchResult[], adresses: () => Response) {
	return vi.fn(async (input: RequestInfo | URL) => {
		const url = new URL(String(input), 'http://origin.invalid');
		return url.pathname === '/v1/adresses/recherche' ? adresses() : json({ statut: 'ok', donnees: communes });
	});
}

async function chargerQ(q: string, fetch: typeof globalThis.fetch) {
	const url = new URL('https://fixture/communes');
	url.searchParams.set('q', q);
	return (await (load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>)({
		url,
		fetch
	})) as CommunesPageData;
}

describe('/communes?q= — a search too broad for its budget (R-08)', () => {
	it('the loader keeps the served state apart from the results and from the outage', async () => {
		const data = await chargerQ('equeurdreville', api([], tropLarge));

		expect(data.adresses).toEqual({ etat: 'trop_large', hebergement: HEBERGE });
	});

	it('nothing found among the communes: « trop large », no outage, no « Relancer », no « Aucune … adresse »', async () => {
		const data = await chargerQ('equeurdreville', api([], tropLarge));
		const { container, queryByRole } = render(Page, { props: { data } });

		expect(container.textContent).toContain(RECHERCHE_TROP_LARGE);
		expect(container.textContent).not.toContain('momentanément indisponible');
		expect(container.textContent).not.toContain('Relancer');
		expect(container.textContent).not.toContain('ni adresse');
		expect(queryByRole('alert')).toBeNull();
	});

	it('communes listed: they stay and are counted alone', async () => {
		const data = await chargerQ('le pin', api([LE_PIN], tropLarge));
		const { container } = render(Page, { props: { data } });

		expect(container.querySelector('li[data-code-insee="35999"]')).not.toBeNull();
		expect(container.textContent).toContain(RECHERCHE_TROP_LARGE);
		expect(container.querySelector('p.r-etat[role="status"]')?.textContent).toBe('1 commune proposée');
	});

	it('témoin : a 503 of the address search stays the outage of its group, with « Relancer »', async () => {
		const data = await chargerQ('le pin', api([LE_PIN], () => json({ title: 'x', status: 503, code: 'geocodage_indisponible' }, 503)));
		const { getByRole, container } = render(Page, { props: { data } });

		expect(data.adresses?.etat).toBe('panne');
		expect(getByRole('alert').textContent).toContain("La recherche d'adresses est momentanément indisponible.");
		expect(getByRole('alert').textContent).toContain('Relancer la recherche');
		expect(container.textContent).not.toContain(RECHERCHE_TROP_LARGE);
	});
});
