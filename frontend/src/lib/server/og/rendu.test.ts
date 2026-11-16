// @vitest-environment node
// Drawing of a link preview: every character the cards write is in the fonts, a long name is cut on two
// lines and never overflows, the text is escaped, and the cost of a drawing is measured.
import { describe, expect, it } from 'vitest';
import metriques from './polices/metriques.json';
import { largeur, lisible, placerNom, placerSources, rendreCarte, svgDeCarte } from './rendu';
import type { CarteOg } from '$lib/seo/og-carte';

const CARTE: CarteOg = {
	genre: 'commune',
	code: '44109',
	nom: 'Nantes',
	surtitre: 'Commune · Loire-Atlantique (44)',
	chiffres: [
		{ libelle: 'Appartements, prix médian', valeur: '3 385 €/m²', source: 'DVF · 3 075 ventes · au 31 déc. 2025', sigle: 'DVF' },
		{ libelle: 'Maisons, prix médian', valeur: '4 120 €/m²', source: 'DVF · 812 ventes · au 31 déc. 2025', sigle: 'DVF' },
		{ libelle: 'Habitants', valeur: '325 070', source: 'INSEE · recensement 2022', sigle: 'INSEE' }
	],
	chemin: '/commune/44109',
	alt: 'Nantes'
};

describe('fonts', () => {
	it('hold every character of the French names and figures the cards write', () => {
		const textes = ['Saint-Étienne-du-Rouvray', 'L’Haÿ-les-Roses', 'Œuilly', 'Châtel-Guyon', 'Île-d’Yeu', 'Département 2A · Corse', '€/m² · – …']
			.map(lisible)
			.join('');
		for (const police of Object.keys(metriques) as (keyof typeof metriques)[]) {
			const avances = metriques[police] as Record<string, number>;
			const manquants = [...textes].filter((c) => !(String(c.codePointAt(0)) in avances));
			expect(manquants, police).toEqual([]);
		}
	});

	it('the narrow no-break space and the arrow, absent from the subsets, are replaced', () => {
		expect(lisible('3 385 · 2024-12-31 → 2025-12-31')).toBe('3 385 · 2024-12-31 – 2025-12-31');
	});
});

describe('placerNom', () => {
	it('keeps a short name on one line, large', () => {
		expect(placerNom('Nantes')).toEqual({ taille: 96, lignes: ['Nantes'] });
	});

	it('cuts a long name after a hyphen, on two lines that hold in the width', () => {
		const { taille, lignes } = placerNom('Saint-Rémy-en-Bouzemont-Saint-Genest-et-Isson');
		expect(lignes).toHaveLength(2);
		expect(lignes.join('')).toBe('Saint-Rémy-en-Bouzemont-Saint-Genest-et-Isson');
		for (const ligne of lignes) expect(largeur(ligne, 'newsreader-500', taille)).toBeLessThanOrEqual(1040);
	});
});

describe('placerSources', () => {
	it('drops the same clauses from every column, never the period of one column only', () => {
		const sources = ['DVF · 29 544 ventes · au 31 déc. 2025', 'DVF · 151 ventes · au 31 déc. 2025', 'INSEE · recensement 2022'];
		expect(placerSources(sources, 315)).toEqual(['DVF · 29 544 ventes', 'DVF · 151 ventes', 'INSEE · recensement 2022']);
		expect(placerSources(sources, 1040)).toEqual(sources);
	});
});

describe('svgDeCarte', () => {
	it('escapes the served text', () => {
		const svg = svgDeCarte({ ...CARTE, nom: '<script>&"' });
		expect(svg).not.toContain('<script>');
		expect(svg).toContain('&#60;script&#62;&#38;&#34;');
	});
});

describe('rendreCarte', () => {
	it('draws a 1200 x 630 PNG in well under a second once warm', async () => {
		await rendreCarte(CARTE);
		const mesures: number[] = [];
		for (let i = 0; i < 5; i++) mesures.push((await rendreCarte(CARTE)).ms);
		const { png } = await rendreCarte(CARTE);
		const octets = Buffer.from(png);
		expect([octets.readUInt32BE(16), octets.readUInt32BE(20)]).toEqual([1200, 630]);
		// A loose bound: a regression of an order of magnitude, not the speed of the machine.
		expect(Math.min(...mesures)).toBeLessThan(500);
	});
});
