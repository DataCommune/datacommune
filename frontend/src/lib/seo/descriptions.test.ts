import { describe, expect, it } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import { COMMUNE_DPE_15 } from '$lib/data/__fixtures__/commune-indicateurs-dpe-15';
import { communeKpis } from '$lib/data/commune-kpi';
import { DESCRIPTION_MAX, deNom, descriptionCommune, premiereQuiTient } from './descriptions';

// Spaces normalised for reading: the figures carry the non-breaking spaces of the formatters.
const espaces = (texte: string) => texte.replace(/[  ]/g, ' ');
const decrire = (commune: CommuneSynthese, nom = 'Challans', dep: string | null = '85') =>
	descriptionCommune(nom, dep, commune, communeKpis(commune));

describe('deNom — « de » contracted with the article of the name', () => {
	it.each([
		['Nantes', 'de Nantes'],
		['Abbaretz', 'd’Abbaretz'],
		['Évreux', 'd’Évreux'],
		['Le Mans', 'du Mans'],
		['Les Sables-d’Olonne', 'des Sables-d’Olonne'],
		['La Baule-Escoublac', 'de La Baule-Escoublac'],
		['L’Haÿ-les-Roses', 'de L’Haÿ-les-Roses'],
		['Hautot-sur-Mer', 'de Hautot-sur-Mer'],
		['Lesneven', 'de Lesneven'],
		['12e arrondissement de Paris', 'du 12e arrondissement de Paris'],
		['1er arrondissement de Lyon', 'du 1er arrondissement de Lyon']
	])('%s → %s', (nom, attendu) => expect(deNom(nom)).toBe(attendu));
});

describe('descriptionCommune — the figures the fiche serves, within the snippet', () => {
	it('names the served median prices, their sales and their period', () => {
		const commune = { ...COMMUNE_DPE_15, population: 21500, annee_demographie: 2022 } as CommuneSynthese;
		const texte = decrire(commune);
		expect(espaces(texte)).toBe(
			'Challans (85), prix médians : appartements 3 121 €/m² (41 ventes), maisons 2 480 €/m² (96 ventes), du 31 décembre 2024 au 31 décembre 2025.'
		);
		expect(texte.length).toBeLessThanOrEqual(DESCRIPTION_MAX);
	});

	it('two communes with different prices get different descriptions', () => {
		const autre = { ...COMMUNE_DPE_15, prix_m2_median_maison: 1656 } as CommuneSynthese;
		expect(decrire(autre, 'Abbaretz', '44')).not.toBe(decrire(COMMUNE_DPE_15, 'Abbaretz', '44'));
	});

	it('a price that is not served is left out, never written as a zero', () => {
		const sansMaison = {
			...COMMUNE_DPE_15,
			prix_m2_median_maison: null,
			etats: { ...COMMUNE_DPE_15.etats, prix_m2_median_maison: { etat: 'insufficient_sample', motif: 'x' } }
		} as CommuneSynthese;
		const texte = decrire(sansMaison);
		expect(texte).toContain('prix médian');
		expect(texte).not.toContain('maisons');
		expect(texte).not.toMatch(/\b0 €|non renseigné/);
	});

	it('without any served price, the population leads and no price is announced as a figure', () => {
		const sansPrix = { ...COMMUNE_DPE_15, fenetre_mediane_dvf: null, population: 2052, annee_demographie: 2022 } as CommuneSynthese;
		const texte = decrire(sansPrix, 'Abbaretz', '44');
		expect(espaces(texte)).toBe('Abbaretz (44) : 2 052 habitants (2022, INSEE). DPE, loyers, revenus et services, avec leurs sources et leurs limites.');
		expect(texte).not.toMatch(/€/);
		expect(texte.length).toBeLessThanOrEqual(DESCRIPTION_MAX);
	});

	it('a served zero population is worded, never « 0 habitants »', () => {
		const morte = { ...COMMUNE_DPE_15, fenetre_mediane_dvf: null, population: 0, annee_demographie: 2022 } as CommuneSynthese;
		const texte = decrire(morte, 'Beaumont-en-Verdunois', '55');
		expect(espaces(texte)).toBe('Beaumont-en-Verdunois (55) : aucun habitant recensé (2022, INSEE). DPE, loyers, revenus et services, avec leurs sources et leurs limites.');
		expect(texte).not.toMatch(/\b0\s*habitants/);
	});

	it('a page without figures names the commune with the right elision', () => {
		expect(descriptionCommune('Abbaretz', '44', null, [])).toBe('Fiche publique DataCommune d’Abbaretz.');
		expect(descriptionCommune('Les Sorinières', '44', null, [])).toBe('Fiche publique DataCommune des Sorinières.');
	});

	it('a long name still fits: the closing sentence, then the population, give way', () => {
		const commune = { ...COMMUNE_DPE_15, population: 1234567, annee_demographie: 2022 } as CommuneSynthese;
		const texte = decrire(commune, 'Saint-Remy-en-Bouzemont-Saint-Genest-et-Isson', '51');
		expect(texte.length).toBeLessThanOrEqual(DESCRIPTION_MAX);
		expect(espaces(texte)).toContain('du 31 décembre 2024 au 31 décembre 2025');
	});
});

describe('premiereQuiTient', () => {
	it('keeps the first candidate within the limit, else the last', () => {
		expect(premiereQuiTient(['x'.repeat(200), 'court'])).toBe('court');
		expect(premiereQuiTient(['x'.repeat(200), 'y'.repeat(180)])).toBe('y'.repeat(180));
	});
});
