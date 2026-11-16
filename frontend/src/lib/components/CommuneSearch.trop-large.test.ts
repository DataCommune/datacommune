// R-08 (2026-11-10): an address search too broad for its budget is served 200 `etat: 'trop_large'`.
// The field says so, without the outage wording nor « Relancer » (the same input meets the same
// budget again), and does not count the addresses as a zero. A real outage keeps both (témoin).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, within } from '@testing-library/svelte';

vi.mock('$lib/api/communes', () => ({ searchCommunes: vi.fn() }));
vi.mock('$lib/api/adresses', () => ({ searchAdresses: vi.fn() }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { searchCommunes, type CommuneSearchResult } from '$lib/api/communes';
import { searchAdresses, type RechercheAdresses } from '$lib/api/adresses';
import { ApiError } from '$lib/api/client';
import { RECHERCHE_TROP_LARGE } from '$lib/data/recherche-lieux';
import CommuneSearch from './CommuneSearch.svelte';

const mockSearch = searchCommunes as unknown as ReturnType<typeof vi.fn>;
const mockAdresses = searchAdresses as unknown as ReturnType<typeof vi.fn>;

const LE_PIN: CommuneSearchResult = {
	code_insee: '35999',
	nom: 'Le Pin',
	code_postal: '35170',
	departement: '35',
	population: 1000,
	population_annee: null,
	population_source: 'geo_communes',
	hebergement: { etat: 'heberge', motif: null, departements: [] }
};

const TROP_LARGE: RechercheAdresses = {
	ordre_types: ['commune', 'adresse'],
	resultats: [],
	hebergement: { ban: { etat: 'heberge', motif: null, departements: ['85'] } },
	correspondance_numero: null,
	etat: 'trop_large'
};

async function chercher(q: string) {
	const view = render(CommuneSearch, { props: {} });
	const input = view.getByRole('combobox', { name: 'Commune, code postal ou adresse' });
	await fireEvent.input(input, { target: { value: q } });
	await vi.advanceTimersByTimeAsync(200);
	await vi.advanceTimersByTimeAsync(0);
	return view;
}

beforeEach(() => {
	vi.useFakeTimers();
	mockSearch.mockReset();
	mockAdresses.mockReset();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('CommuneSearch — a search too broad for its budget (R-08)', () => {
	it('the wording is the one validated by Ianis (2026-11-10), one constant', () => {
		expect(RECHERCHE_TROP_LARGE).toBe('Recherche trop large pour aboutir : ajoutez la commune ou le code postal.');
	});

	it('nothing found among the communes: the field says « trop large », never an outage nor a zero of addresses', async () => {
		mockSearch.mockResolvedValue({ donnees: [] });
		mockAdresses.mockResolvedValue(TROP_LARGE);
		const { container, getByRole, queryByText } = await chercher('equeurdreville');

		expect(container.textContent).toContain(RECHERCHE_TROP_LARGE);
		expect(queryByText(/momentanément indisponible/)).toBeNull();
		expect(queryByText(/Relancer/)).toBeNull();
		// The addresses were not searched to the end: no « ni adresse ».
		expect(container.textContent).not.toContain('ni adresse');
		expect(getByRole('status').textContent).toContain(RECHERCHE_TROP_LARGE);
	});

	it('communes listed: they stay, the addresses are not counted', async () => {
		mockSearch.mockResolvedValue({ donnees: [LE_PIN] });
		mockAdresses.mockResolvedValue(TROP_LARGE);
		const { container, getByRole } = await chercher('le pin');

		const options = within(getByRole('listbox')).getAllByRole('option');
		expect(options[0].textContent).toContain('Le Pin');
		expect(options[0].textContent).not.toContain('Relancer');
		expect(container.textContent).toContain(RECHERCHE_TROP_LARGE);
		expect(getByRole('status').textContent).toContain('1 commune proposée');
	});

	it('témoin : a real outage of the address search keeps its outage line and « Relancer »', async () => {
		mockSearch.mockResolvedValue({ donnees: [LE_PIN] });
		mockAdresses.mockRejectedValue(new ApiError(503, 'x', false, 'Recherche d’adresses momentanement indisponible'));
		const { container, getByRole } = await chercher('le pin');

		const options = within(getByRole('listbox')).getAllByRole('option');
		expect(options[0].textContent).toContain("La recherche d'adresses est momentanément indisponible.");
		expect(options[0].textContent).toContain('Relancer la recherche');
		expect(container.textContent).not.toContain(RECHERCHE_TROP_LARGE);
	});
});
