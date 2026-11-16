// List of values `dl.tab` (04.8-04, UI-SPEC v4 § 3.13): one row per value, the term in Inter 400, the
// value rendered by StateValue (an absence keeps its served word, never a zero), the note, the
// field id kept, and the button opening the panel of the row, where the source is read (4.9, lot 3).
import { render } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { describe, expect, it } from 'vitest';
import ListeValeurs, { type ValeurDeListe } from './ListeValeurs.svelte';

const VALEURS: ValeurDeListe[] = [
	{ id: 'population', libelle: 'Population', fieldId: 'demographie.population', etat: { kind: 'valeur', texte: '21 516', unite: 'habitants' }, note: '2021' },
	{ id: 'zero', libelle: 'Monuments', fieldId: 'patrimoine.nb_monuments', etat: { kind: 'valeur', texte: '0', unite: null } },
	{
		id: 'fibre',
		libelle: 'Fibre',
		fieldId: 'fibre.pct',
		etat: { kind: 'absence', etat: 'not_reported', motif: 'Aucune ligne de la source pour cette commune.', effectif: null },
		volet: [{ terme: 'Définition', valeur: 'Part des locaux raccordables.' }],
		brut: []
	}
];

describe('ListeValeurs', () => {
	it('renders one .tab-l row per value inside a dl.tab, each keeping its field id', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: VALEURS } });
		const dl = container.querySelector('dl.tab')!;
		const lignes = [...dl.querySelectorAll(':scope > .tab-l')];
		expect(lignes).toHaveLength(3);
		expect(lignes.map((l) => l.getAttribute('data-field-id'))).toEqual(['demographie.population', 'patrimoine.nb_monuments', 'fibre.pct']);
		expect(lignes.map((l) => l.querySelector('dt')?.textContent)).toEqual(['Population', 'Monuments', 'Fibre']);
	});

	it('renders the value through StateValue: a zero is a value, an absence its served word, never 0', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: VALEURS } });
		const [population, zero, fibre] = [...container.querySelectorAll('.tab-l')];
		expect(population.querySelector('.tab-v [data-state="value"]')?.textContent).toBe('21 516 habitants');
		expect(zero.querySelector('.tab-v [data-state="value"]')?.textContent).toBe('0');
		expect(fibre.querySelector('.tab-v [data-state="value"]')).toBeNull();
		expect(fibre.querySelector('.tab-v .state-badge')?.textContent).toBe('Non renseigné');
		expect(fibre.querySelector('.tab-v')?.textContent).not.toMatch(/\b0\b/);
	});

	it('writes the note, the period of the value', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: VALEURS } });
		const notes = [...container.querySelectorAll('.tab-l')].map((l) => l.querySelector('.tab-note')?.textContent ?? null);
		expect(notes).toEqual(['2021', null, null]);
	});

	it('passes the form of the button to each row: one control per row at most', () => {
		const { container, getAllByRole } = render(ListeValeurs, { props: { valeurs: VALEURS, forme: 'detail' } });
		expect(getAllByRole('button', { name: 'Détail : Fibre' })).toHaveLength(1);
		for (const ligne of container.querySelectorAll('.tab-l')) expect(ligne.querySelectorAll('button.source-button').length).toBeLessThanOrEqual(1);
	});

	it('gives a row with a source panel its « Source » button, which opens the panel in one action', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: VALEURS } });
		const boutons = container.querySelectorAll('.tab-l button.source-button');
		expect(boutons).toHaveLength(1);
		expect(boutons[0].getAttribute('aria-label')).toBe('Source et définition : Fibre');
		expect(container.querySelectorAll('.tab-l dialog')).toHaveLength(1);
	});
});

// P7 of the DPE document page (2026-11-10): a row whose value carries links (an address, a dataset, a
// replacing document) gives its own content; the row, its term, its field id and its « i » are unchanged.
describe('ListeValeurs — contenu fourni par la page', () => {
	const contenu = createRawSnippet(() => ({ render: () => '<span class="contenu-de-page"><a href="/adresse/1">12 Rue des Lilas</a></span>' }));

	it('without `contenu`, the value is rendered by StateValue, as before', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: VALEURS.slice(0, 1) } });
		expect(container.querySelector('.tab-v [data-state="value"]')?.textContent).toBe('21 516 habitants');
		expect(container.querySelector('.contenu-de-page')).toBeNull();
	});

	it('with `contenu`, the content takes the place of StateValue in the value; term, id and « i » stay', () => {
		const valeur: ValeurDeListe = {
			id: 'adresse-ban',
			libelle: 'Adresse de la BAN',
			etat: { kind: 'valeur', texte: '12 Rue des Lilas', unite: null },
			contenu,
			volet: [{ terme: 'Définition', valeur: 'Adresse retenue.' }],
			brut: []
		};
		const { container, getByRole } = render(ListeValeurs, { props: { valeurs: [valeur, VALEURS[1]] } });
		const [ligne, autre] = [...container.querySelectorAll('.tab-l')];
		expect(ligne.getAttribute('data-ligne')).toBe('adresse-ban');
		expect(ligne.querySelector('dt')?.textContent).toBe('Adresse de la BAN');
		expect(ligne.querySelector('.tab-v .contenu-de-page a')?.getAttribute('href')).toBe('/adresse/1');
		expect(ligne.querySelector('.tab-v .state-value')).toBeNull();
		expect(getByRole('button', { name: 'Source et définition : Adresse de la BAN' })).toBeTruthy();
		expect(autre.querySelector('.tab-v [data-state="value"]')?.textContent).toBe('0');
	});
});
