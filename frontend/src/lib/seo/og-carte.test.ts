// What a link preview says: only served figures, read through the page's own functions, an absent value
// left out and never written as a zero, no card at all when nothing is served (generic image).
import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '$lib/data/__fixtures__/commune-85191-20261012';
import { DEPARTEMENT_44 } from '$lib/data/__fixtures__/departement';
import { communeKpis } from '$lib/data/commune-kpi';
import { carteCommune, carteDepartement, urlImageOg } from './og-carte';

const SYNTHESE = COMMUNE_85191_OBSERVED as unknown as CommuneSynthese;
const SOURCES = COMMUNE_85191_SOURCES as unknown as SourceAttribution[];
const DVF = [{ nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0' }];

/** Prices and population declared absent, as the API serves them: a null and its state. */
const SANS_CHIFFRE: CommuneSynthese = {
	...SYNTHESE,
	indicateurs_dvf: null,
	prix_m2_median: null,
	prix_m2_median_maison: null,
	prix_m2_median_appartement: null,
	population: null
};

describe('carteCommune', () => {
	it('writes the two medians and the population the page shows, each with its source', () => {
		const carte = carteCommune({ code: '85191', synthese: SYNTHESE, sources: SOURCES })!;
		expect(carte).toMatchObject({ genre: 'commune', code: '85191', nom: 'La Roche-sur-Yon', surtitre: 'Commune · Vendée (85)', chemin: '/commune/85191' });
		expect(carte.chiffres).toEqual([
			{ libelle: 'Appartements, prix médian', valeur: '2 429 €/m²', source: 'DVF · 375 ventes · au 31 déc. 2025', sigle: 'DVF' },
			{ libelle: 'Maisons, prix médian', valeur: '2 357 €/m²', source: 'DVF · 460 ventes · au 31 déc. 2025', sigle: 'DVF' },
			{ libelle: 'Habitants', valeur: '54 699', source: 'INSEE · recensement 2022', sigle: 'INSEE' }
		]);
		// The same figure as the tile of the page, character for character.
		const tuile = communeKpis(SYNTHESE, SOURCES).find((kpi) => kpi.id === 'prix-appartements')!.lignes[0].etat;
		expect(tuile).toMatchObject({ kind: 'valeur', texte: '2 429', unite: '€/m²' });
		expect(carte.alt).toBe(
			'La Roche-sur-Yon — Commune · Vendée (85). Appartements, prix médian : 2 429 €/m² ; Maisons, prix médian : 2 357 €/m² ; Habitants : 54 699. Sources : DVF, INSEE.'
		);
	});

	it('leaves an absent price out, never as a zero', () => {
		const carte = carteCommune({ code: '85191', synthese: { ...SYNTHESE, indicateurs_dvf: null, prix_m2_median_maison: null }, sources: SOURCES })!;
		expect(carte.chiffres.map((c) => c.libelle)).not.toContain('Maisons, prix médian');
		expect(carte.chiffres.every((c) => !/^0(\D|$)/.test(c.valeur))).toBe(true);
	});

	it('has no card when no figure is served: the generic image stands', () => {
		expect(carteCommune({ code: '85191', synthese: SANS_CHIFFRE, sources: SOURCES })).toBeNull();
	});

	it('names an arrondissement as the page does', () => {
		const carte = carteCommune({
			code: '75112',
			parent: { code: '75056', nom: 'Paris' },
			synthese: { ...SYNTHESE, code_insee: '75112', nom_commune: 'Paris 12e Arrondissement', code_departement: '75' },
			sources: SOURCES
		})!;
		expect(carte.nom).toBe('12e arrondissement de Paris');
		expect(carte.surtitre).toBe('Arrondissement · Paris (75)');
	});
});

describe('carteDepartement', () => {
	it('writes the served figures of the departement, the population without its unit, and their sources', () => {
		const carte = carteDepartement({ code: '44', synthese: DEPARTEMENT_44, sources: DVF })!;
		expect(carte).toMatchObject({ genre: 'departement', nom: 'Loire-Atlantique', surtitre: 'Département 44 · Pays de la Loire', chemin: '/departement/44' });
		expect(carte.chiffres).toEqual([
			{ libelle: 'Appartements, prix médian', valeur: '3 480 €/m²', source: 'DVF · 7 350 ventes · au 30 juin 2025', sigle: 'DVF' },
			{ libelle: 'Habitants', valeur: '1 450 000', source: 'INSEE · recensement 2022', sigle: 'INSEE' }
		]);
	});

	it('has no card when the departement serves no figure', () => {
		const vide = {
			...DEPARTEMENT_44,
			population: { ...DEPARTEMENT_44.population, total: null },
			prix: {
				...DEPARTEMENT_44.prix,
				appartement: { ...DEPARTEMENT_44.prix.appartement, mediane_m2: null },
				maison: { ...DEPARTEMENT_44.prix.maison, mediane_m2: null }
			}
		};
		expect(carteDepartement({ code: '44', synthese: vide, sources: DVF })).toBeNull();
	});
});

it('urlImageOg', () => {
	expect(urlImageOg('https://datacommune.fr', { genre: 'commune', code: '2A004' })).toBe('https://datacommune.fr/og/commune/2A004.png');
	expect(urlImageOg('https://datacommune.fr', { genre: 'departement', code: '44' })).toBe('https://datacommune.fr/og/departement/44.png');
});
