// « Comparer avec une autre commune » on a commune fiche (review of 2026-11-10, R-05) and the entry to
// the comparison of arrondissements (Paris, Lyon, Marseille). The action adds the commune to the tab
// selection and opens the comparator; a full selection refuses the commune, and the fiche says so
// instead of opening a comparison the commune is absent from.
import { fireEvent, render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const runtime = vi.hoisted(() => ({ goto: vi.fn() }));
vi.mock('$app/environment', () => ({ browser: true }));
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => runtime.goto(...args), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://site.invalid/commune/85047'), state: {} })
}));

import { COMMUNE_85047_CHALLANS } from '$lib/data/__fixtures__/commune-85';
import type { CommuneSynthese } from '$lib/api/commune';
import { comparisonSelection } from '$lib/stores/comparison-selection.svelte';
import type { FicheCommuneData } from './+page';
import Page from './+page.svelte';

const fiche = (code: string, synthese: Partial<CommuneSynthese> = {}, parent: FicheCommuneData['parent'] = null): FicheCommuneData => ({
	etat: 'fiche',
	code,
	parent,
	synthese: { ...COMMUNE_85047_CHALLANS, code_insee: code, ...synthese } as CommuneSynthese,
	sources: null,
	consulteLe: '10 novembre 2026'
});

beforeEach(() => {
	runtime.goto.mockReset();
	comparisonSelection.replace([]);
});
afterEach(() => {
	comparisonSelection.replace([]);
	document.head.innerHTML = '';
});

// Asked by the owner on 2026-11-10: a visible entry to compare arrondissements, from the fiche of the
// commune (Paris, Lyon, Marseille) and from the fiche of each arrondissement.
describe('/commune/[code] — entry to the comparison of arrondissements', () => {
	const entree = (container: HTMLElement) =>
		[...container.querySelectorAll('.fiche-header details > summary')].find((resume) => resume.textContent?.includes('Comparer des arrondissements')) ?? null;
	const coches = (container: HTMLElement) =>
		[...container.querySelectorAll<HTMLInputElement>('.fiche-header input[name="communes"]:checked')].map((element) => element.value);

	it('the fiche of Paris offers its twenty arrondissements, none chosen', () => {
		const { container } = render(Page, { props: { data: fiche('75056', { nom_commune: 'Paris', code_departement: '75' }) } });
		expect(entree(container)?.textContent?.trim()).toBe('Comparer des arrondissements de Paris');
		expect(container.querySelectorAll('.fiche-header input[name="communes"]')).toHaveLength(20);
		expect(coches(container)).toEqual([]);
	});

	it('the fiche of an arrondissement offers those of its commune, its own already chosen', () => {
		const data = fiche(
			'69383',
			{ nom_commune: 'Lyon 3e Arrondissement', code_departement: '69', type_zone: 'arrondissement', commune_parent: '69123' },
			{ code: '69123', nom: 'Lyon' }
		);
		const { container } = render(Page, { props: { data } });
		expect(entree(container)?.textContent?.trim()).toBe('Comparer des arrondissements de Lyon');
		expect(container.querySelectorAll('.fiche-header input[name="communes"]')).toHaveLength(9);
		expect(coches(container)).toEqual(['69383']);
	});

	it('an ordinary commune has no such entry', () => {
		const { container } = render(Page, { props: { data: fiche('85047') } });
		expect(entree(container)).toBeNull();
	});
});

describe('/commune/[code] — « Comparer avec une autre commune » (R-05)', () => {
	it('adds the commune to the selection and opens the comparator on it', async () => {
		comparisonSelection.replace(['44109']);
		const { getByRole } = render(Page, { props: { data: fiche('85047') } });
		await fireEvent.click(getByRole('link', { name: 'Comparer avec une autre commune' }));
		expect(comparisonSelection.codes).toEqual(['44109', '85047']);
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44109,85047');
	});

	it('a full selection: the commune is refused in words, with the way to the comparator, and nothing opens', async () => {
		const pleine = ['44109', '85191', '85194', '44184'];
		comparisonSelection.replace(pleine);
		const { getByRole, container } = render(Page, { props: { data: fiche('85047') } });
		await fireEvent.click(getByRole('link', { name: 'Comparer avec une autre commune' }));
		expect(runtime.goto).not.toHaveBeenCalled();
		expect(comparisonSelection.codes).toEqual(pleine);
		const statut = container.querySelector('.fiche-comparer-statut');
		expect(statut?.getAttribute('role')).toBe('status');
		// The sentence of the directory (`confirmationAjout`), word for word.
		expect(statut?.textContent).toContain('Quatre communes au maximum : retirez-en une pour en ajouter une autre.');
		expect(statut?.querySelector('a')?.getAttribute('href')).toBe('/comparer?communes=44109,85191,85194,44184');
	});

	it('a commune already in the selection opens the comparator without a refusal', async () => {
		comparisonSelection.replace(['44109', '85047']);
		const { getByRole, container } = render(Page, { props: { data: fiche('85047') } });
		await fireEvent.click(getByRole('link', { name: 'Comparer avec une autre commune' }));
		expect(runtime.goto).toHaveBeenCalledWith('/comparer?communes=44109,85047');
		expect(container.querySelector('.fiche-comparer-statut')?.textContent?.trim()).toBe('');
	});
});
