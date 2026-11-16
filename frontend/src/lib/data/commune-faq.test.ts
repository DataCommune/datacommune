import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, IndicateursDpe, ResultatDpeIndicateur } from '$lib/api/commune';
import { COMMUNE_DPE_15, DPE_BLOCK_15 } from './__fixtures__/commune-indicateurs-dpe-15';
import { communeKpis } from './commune-kpi';
import { faqCommune, faqJsonLd } from './commune-faq';

const NOM = 'Challans';
const RESULTAT = DPE_BLOCK_15.resultat as ResultatDpeIndicateur;

function reponse(commune: CommuneSynthese, id: string): string | undefined {
	return faqCommune(commune, null, NOM).find((item) => item.id === id)?.reponse;
}

describe('faqCommune — computed answers (04.7-15 T4)', () => {
	it('asks the five questions when every value is served, in a fixed order', () => {
		expect(faqCommune(COMMUNE_DPE_15, null, NOM).map((item) => item.id)).toEqual([
			'prix-maisons', 'prix-appartements', 'part-fg', 'classe-frequente', 'population'
		]);
	});

	it('a price answer cites the served value, size and period of its marker, word for word', () => {
		const ligne = communeKpis(COMMUNE_DPE_15).find((item) => item.id === 'prix-maisons')!.lignes[0];
		expect(ligne.etat.kind).toBe('valeur');
		const texte = reponse(COMMUNE_DPE_15, 'prix-maisons')!;
		expect(texte).toBe(`Le prix médian au mètre carré des maisons vendues dans la commune est de 2 480 €/m² (${ligne.effectif}, ${ligne.fenetre}).`);
		expect(reponse(COMMUNE_DPE_15, 'prix-appartements')).toContain('des appartements vendus dans la commune est de 3 121 €/m²');
	});

	it('the DPE answers cite the served share, mode and known classes', () => {
		expect(reponse(COMMUNE_DPE_15, 'part-fg')).toBe(
			'12,5 % des 3 456 diagnostics de performance énergétique de classe connue sont classés F ou G. Un DPE est un document, pas un logement : ce n’est pas une part du parc de logements.'
		);
		expect(reponse(COMMUNE_DPE_15, 'classe-frequente')).toBe('La classe E est la plus fréquente parmi les 3 456 diagnostics de classe connue.');
	});

	it('the population answer cites the served figure and its year', () => {
		const commune = { ...COMMUNE_DPE_15, population: 21500, annee_demographie: 2022 } as CommuneSynthese;
		expect(reponse(commune, 'population')).toBe('Challans compte 21 500 habitants (2022, INSEE).');
	});

	it('a served zero population is worded as such, never « 0 habitants » (55039, SEO 2026-11-14)', () => {
		const vide = { ...COMMUNE_DPE_15, population: 0, annee_demographie: 2022 } as CommuneSynthese;
		expect(reponse(vide, 'population')).toBe('Challans ne compte aucun habitant recensé (2022, INSEE).');
		const seul = { ...COMMUNE_DPE_15, population: 1, annee_demographie: 2022 } as CommuneSynthese;
		expect(reponse(seul, 'population')).toBe('Challans compte 1 habitant (2022, INSEE).');
	});

	it('a question whose value is not served is left out, never answered by a zero', () => {
		const sansPrix = { ...COMMUNE_DPE_15, prix_m2_median_maison: null, etats: { ...COMMUNE_DPE_15.etats, prix_m2_median_maison: { etat: 'insufficient_sample', motif: 'x' } } } as CommuneSynthese;
		const sansPart = { ...COMMUNE_DPE_15, indicateurs_dpe: { ...DPE_BLOCK_15, resultat: { ...RESULTAT, part_fg_pct: { etat: 'zero_denominator', motif: 'x' } } } as IndicateursDpe };
		const sansDpe = { ...COMMUNE_DPE_15, indicateurs_dpe: null };
		const sansPopulation = { ...COMMUNE_DPE_15, population: null, etats: { ...COMMUNE_DPE_15.etats, population: { etat: 'not_reported', motif: 'x' } } } as CommuneSynthese;
		expect(faqCommune(sansPrix, null, NOM).map((item) => item.id)).not.toContain('prix-maisons');
		expect(faqCommune(sansPart, null, NOM).map((item) => item.id)).not.toContain('part-fg');
		expect(faqCommune(sansDpe, null, NOM).map((item) => item.id)).not.toContain('classe-frequente');
		expect(faqCommune(sansPopulation, null, NOM).map((item) => item.id)).not.toContain('population');
		// A served price without a served window: the answer would say « non renseignée » (04.7-15 review).
		const sansFenetre = { ...COMMUNE_DPE_15, fenetre_mediane_dvf: null } as CommuneSynthese;
		expect(faqCommune(sansFenetre, null, NOM).map((item) => item.id)).not.toContain('prix-maisons');
		for (const commune of [sansPrix, sansPart, sansDpe, sansPopulation, sansFenetre]) {
			for (const item of faqCommune(commune, null, NOM)) expect(item.reponse).not.toMatch(/non renseigné|indisponible|\b0 €/i);
		}
	});
});

describe('faqJsonLd — the visible text, word for word', () => {
	it('one FAQPage whose questions and answers are the visible ones', () => {
		const questions = faqCommune(COMMUNE_DPE_15, null, NOM);
		const jsonLd = faqJsonLd(questions) as { '@type': string; mainEntity: { name: string; acceptedAnswer: { text: string } }[] };
		expect(jsonLd['@type']).toBe('FAQPage');
		expect(jsonLd.mainEntity.map((item) => [item.name, item.acceptedAnswer.text])).toEqual(questions.map((item) => [item.question, item.reponse]));
	});

	it('no question, no FAQPage', () => {
		expect(faqJsonLd([])).toBeNull();
	});
});

describe('faqCommune — wording', () => {
	it('the name opens each question, never after « à » or « de » (no elision to make)', () => {
		for (const item of faqCommune(COMMUNE_DPE_15, null, 'Le Mans')) {
			expect(item.question.startsWith('Le Mans : ')).toBe(true);
			expect(item.question.endsWith(' ?')).toBe(true);
			expect(`${item.question} ${item.reponse}`).not.toMatch(/(?:^|\s)(?:à|de) Le Mans/);
		}
	});
});

describe('faqCommune — arrondissement wording (04.7-09)', () => {
	const arrondissement = { ...COMMUNE_DPE_15, type_zone: 'arrondissement' as const, commune_parent: '75056' };
	const NOM_ARR = 'Paris 12e arrondissement';

	it('speaks of the arrondissement, never of the commune, in the visible text and in the JSON-LD', () => {
		const questions = faqCommune(arrondissement, null, NOM_ARR);
		expect(questions.map((q) => q.id)).toContain('population');
		for (const item of questions) expect(`${item.question} ${item.reponse}`).not.toMatch(/\bcommune\b/i);
		expect(questions.find((q) => q.id === 'population')?.question).toContain('compte l’arrondissement');
		expect(questions.find((q) => q.id === 'prix-maisons')?.reponse).toContain('dans l’arrondissement');
		const jsonLd = faqJsonLd(questions) as { mainEntity: { name: string; acceptedAnswer: { text: string } }[] };
		const texte = jsonLd.mainEntity.map((m) => `${m.name} ${m.acceptedAnswer.text}`).join(' ');
		expect(texte).not.toMatch(/\bcommune\b/i);
		expect(texte).toContain('compte l’arrondissement');
	});

	it('a commune keeps its former wording', () => {
		const questions = faqCommune(COMMUNE_DPE_15, null, NOM);
		expect(questions.find((q) => q.id === 'population')?.question).toContain('compte la commune');
		expect(questions.find((q) => q.id === 'prix-maisons')?.reponse).toContain('dans la commune');
	});
});
