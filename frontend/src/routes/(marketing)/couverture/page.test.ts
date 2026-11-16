// /couverture says the rule the pages serve (plan 04.5-08, C3): the sales declared at an address are
// attached by the declared number and suffix (`regle=numero_et_suffixe`), so the sentence saying that
// the « 2 » may show the sales of the « 2 bis » is gone. New text, to be read in a block by Ianis (R10).
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import Page from './+page.svelte';

const texteDeLaPage = () => (render(Page).container.textContent ?? '').replace(/\s+/g, ' ');

// Owner decision of 2026-11-09: the page still said « Loire-Atlantique (44) et Vendée (85) seulement » a week
// after the national switch (101 departements served since 2026-11-02).
describe('/couverture — France entière (décision du 2026-11-09)', () => {
	it('dit la couverture servie : 101 départements, et la couverture propre à chaque jeu', () => {
		const texte = texteDeLaPage();
		expect(texte).toContain('Les données sont servies pour toute la France : 101 départements, outre-mer compris.');
		expect(texte).toContain(
			'Chaque jeu de données a sa propre couverture : les ventes (DVF) ne couvrent ni la Moselle, ni le Bas-Rhin, ni le Haut-Rhin, ni Mayotte.'
		);
		expect(texte).toContain('Quand un jeu ne couvre pas un lieu, la fiche affiche « hors couverture ».');
		expect(texte).toContain('une absence de couverture ne vaut jamais zéro.');
	});

	it('ne dit plus que deux départements seulement sont servis', () => {
		const texte = texteDeLaPage();
		expect(texte).not.toContain('seulement');
		expect(texte).not.toContain('Le reste de la France arrive plus tard');
		expect(texte).not.toContain('hors de ces deux départements');
	});

	it('borne la part des adresses sans ventes rattachées aux deux départements mesurés', () => {
		expect(texteDeLaPage()).toContain(
			"C'est le cas d'environ 24 % des adresses en Loire-Atlantique et 38 % en Vendée ; la part nationale n'est pas mesurée."
		);
	});

	it('date la page du mois de sa mise à jour, et sa description ne nomme plus deux départements', () => {
		expect(texteDeLaPage()).toContain('Dernière mise à jour : novembre 2026');
		const description = document.head.querySelector('meta[name="description"]')?.getAttribute('content') ?? '';
		expect(description).toContain('France entière');
		expect(description).not.toContain('Vendée');
	});
});

describe('/couverture — ventes déclarées par numéro et suffixe (04.5-08, C3)', () => {
	it('décrit la règle servie : le numéro et le suffixe déclarés, le « 2 bis » jamais sur le « 2 »', () => {
		const texte = texteDeLaPage();
		expect(texte).toContain('Le numéro et son suffixe sont distingués.');
		expect(texte).toContain(
			'Les ventes déclarées à une adresse y sont rattachées par le numéro et le suffixe déclarés dans les demandes de valeurs foncières (DVF) : celles du « 2 bis » ne figurent pas sur la page du « 2 ».'
		);
		expect(texte).toContain("Quand un suffixe ne correspond à aucune adresse, ou à plusieurs, la vente n'est montrée sur aucune.");
	});

	it('ne dit plus qu’un numéro sans suffixe regroupe ses variantes', () => {
		const texte = texteDeLaPage();
		expect(texte).not.toContain('Un numéro sans suffixe regroupe ses variantes');
		expect(texte).not.toContain('peuvent donc inclure celles du « 2 bis »');
	});
});
