// Two optional props of `ListeValeurs` for Paris, Lyon, Marseille (R-09): `echelle`, the scale mark of a value
// served at another scale than the page (the existing `RepereEchelle`, said in clear on paper), and `lien`, where
// a value absent here is published. Without them, a row renders exactly as before: the witness below compares
// the DOM of the rows of `ListeValeurs.test.ts` with a snapshot recorded before the props existed. Svelte adds an
// empty anchor comment and a blank text node per `{#if}`; neither renders (`dd` is a grid, whose blank text
// nodes make no box), so the snapshot drops the comments and folds the blanks between tags.
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import ListeValeurs, { type ValeurDeListe } from './ListeValeurs.svelte';

const elementsSeuls = (html: string) => html.replace(/<!---->/g, '').replace(/>\s+</g, '><');

const SANS: ValeurDeListe[] = [
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

const HERITEE: ValeurDeListe = {
	id: 'zone',
	libelle: 'Zone ABC',
	fieldId: 'zonage_abc.zone',
	etat: { kind: 'valeur', texte: 'Abis', unite: null },
	echelle: { pictogramme: 'commune', mot: 'Commune', enClair: 'Commune de Paris' }
};

const RENVOYEE: ValeurDeListe = {
	id: 'loyer',
	libelle: 'Loyer des appartements',
	fieldId: 'loyers.loyer_m2_appartement',
	etat: { kind: 'absence', etat: 'not_reported', motif: 'x', effectif: null, aide: 'Publié par arrondissement : voir les arrondissements de Paris.' },
	lien: { href: '#arrondissements', texte: 'Arrondissements de Paris' }
};

describe('ListeValeurs — échelle et lien (R-09)', () => {
	it('témoin : sans les deux props, le DOM des lignes est celui d’avant', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: SANS, forme: 'detail' } });
		expect(elementsSeuls(container.innerHTML)).toMatchSnapshot();
		expect(container.querySelectorAll('.ech, .tab-echelle, .tab-lien')).toHaveLength(0);
	});

	it('marque la valeur héritée du repère d’échelle existant, et la dit en clair pour le papier', () => {
		const { container } = render(ListeValeurs, { props: { valeurs: [HERITEE, ...SANS] } });
		const [zone, population] = [...container.querySelectorAll('.tab-l')];
		const repere = zone.querySelector('dd .tab-echelle .ech');
		expect(repere?.textContent?.trim()).toBe('Commune');
		expect(repere?.querySelector('svg')).not.toBeNull();
		expect(zone.querySelector('.tab-echelle-clair')?.textContent).toBe('Commune de Paris');
		expect(population.querySelector('.tab-echelle')).toBeNull();
	});

	it('donne au renvoi un lien sous la valeur, dans la même cellule que la valeur', () => {
		const { container, getByRole } = render(ListeValeurs, { props: { valeurs: [RENVOYEE] } });
		const lien = getByRole('link', { name: 'Arrondissements de Paris' });
		expect(lien.getAttribute('href')).toBe('#arrondissements');
		expect(lien.classList.contains('tab-lien')).toBe(true);
		expect(lien.closest('dd')).toBe(container.querySelector('.tab-l dd'));
		expect(container.querySelector('.tab-l dt')?.textContent).toBe('Loyer des appartements');
	});
});
