import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, IndicateursDpe, ResultatDpeIndicateur } from '$lib/api/commune';
import { COMMUNE_DPE_15, DPE_BLOCK_15 } from './__fixtures__/commune-indicateurs-dpe-15';
import { communeKpis } from './commune-kpi';
import { repartitionDpe } from './commune-repartition-dpe';

const RESULTAT = DPE_BLOCK_15.resultat as ResultatDpeIndicateur;

function avec(resultat: Partial<ResultatDpeIndicateur>): CommuneSynthese {
	return { ...COMMUNE_DPE_15, indicateurs_dpe: { ...DPE_BLOCK_15, resultat: { ...RESULTAT, ...resultat } } as IndicateursDpe };
}

describe('repartitionDpe — bars of the served counts (04.7-15 T2)', () => {
	it('one bar per class A→G, with the served count, and the bars add up to the served known classes', () => {
		const repartition = repartitionDpe(COMMUNE_DPE_15)!;
		expect(repartition.barres.map((barre) => barre.classe)).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G']);
		expect(repartition.barres.map((barre) => barre.effectif)).toEqual([40, 120, 610, 1071, 1131, 300, 184]);
		expect(repartition.barres.reduce((total, barre) => total + barre.effectif, 0)).toBe(RESULTAT.population.classes_connues);
		expect(repartition.barres.find((barre) => barre.classe === 'D')!.texte).toBe('1 071');
	});

	it('the longest bar is the largest served count; a served zero is an empty bar, kept', () => {
		const repartition = repartitionDpe(avec({ distribution: { classes: { A: 0, B: 0, C: 2, D: 1, E: 2, F: 0, G: 0 }, inconnue: 0 }, population: { ...RESULTAT.population, classes_connues: 5 }, modes: ['C', 'E'] }))!;
		expect(repartition.barres.map((barre) => barre.longueur)).toEqual([0, 0, 1, 0.5, 1, 0, 0]);
		expect(repartition.barres[0].texte).toBe('0');
	});

	it('the reading sentence cites the served known classes, mode and F/G share, nothing computed', () => {
		expect(repartitionDpe(COMMUNE_DPE_15)!.lecture).toBe(
			'Sur 3 456 diagnostics de classe connue, la classe la plus fréquente est E, avec 1 131 diagnostics ; 12,5 % sont classés F ou G.'
		);
	});

	it('the sentence and the bars say the same figures as marker ④', () => {
		const repartition = repartitionDpe(COMMUNE_DPE_15)!;
		const dominant = communeKpis(COMMUNE_DPE_15).find((item) => item.id === 'dpe-dominant')!;
		expect(dominant.lignes[0].classes).toEqual(['E']);
		expect(repartition.lecture).toContain('est E, avec');
		expect(dominant.lignes[0].effectif).toBe('sur 3\u202f456\u00a0diagnostics de classe connue');
	});

	it('ex aequo modes are said together, with the served count of each', () => {
		const lecture = repartitionDpe(avec({ distribution: { classes: { A: 0, B: 0, C: 2, D: 1, E: 2, F: 0, G: 0 }, inconnue: 0 }, population: { ...RESULTAT.population, classes_connues: 5 }, modes: ['C', 'E'], part_fg_pct: { etat: 'value', valeur: 0 } }))!.lecture;
		expect(lecture).toBe('Sur 5 diagnostics de classe connue, les classes les plus fréquentes, à égalité, sont C et E, avec 2 diagnostics chacune ; 0 % sont classés F ou G.');
	});

	it('an absent F/G share is left out of the sentence, never said as zero', () => {
		const lecture = repartitionDpe(avec({ part_fg_pct: { etat: 'not_reported', motif: 'x' } }))!.lecture;
		expect(lecture).toBe('Sur 3 456 diagnostics de classe connue, la classe la plus fréquente est E, avec 1 131 diagnostics.');
		expect(lecture).not.toMatch(/F ou G|0 %/);
	});

	it('the unknown classes are said apart, the bounds are spelled in full', () => {
		const repartition = repartitionDpe(COMMUNE_DPE_15)!;
		expect(repartition.sansClasse).toBe('56 diagnostics sans classe connue ne figurent pas dans le graphique.');
		expect(repartition.bornes).toBe('Dates d’établissement observées\u00a0: du 1er juillet 2021 au 15 juillet 2026.');
		expect(repartitionDpe(avec({ dates_etablissement_inconnues: 3 }))!.bornes).toBe('Dates d’établissement observées\u00a0: du 1er juillet 2021 au 15 juillet 2026\u202f; 3\u00a0diagnostics sans date connue.');
		expect(repartitionDpe(avec({ distribution: { ...RESULTAT.distribution, inconnue: 0 } }))!.sansClasse).toBeNull();
		expect(repartitionDpe(avec({ date_etablissement_min: null }))!.bornes).toBeNull();
	});
});

describe('repartitionDpe — no chart without a served distribution', () => {
	it.each<[string, CommuneSynthese]>([
		['null block', { ...COMMUNE_DPE_15, indicateurs_dpe: null }],
		['absent block', { ...COMMUNE_DPE_15, indicateurs_dpe: { etat: 'out_of_coverage', motif: 'x' } } as unknown as CommuneSynthese],
		['absent result', { ...COMMUNE_DPE_15, indicateurs_dpe: { ...DPE_BLOCK_15, resultat: { etat: 'not_reported', motif: 'x' } } } as CommuneSynthese],
		['zero known class', avec({ distribution: { classes: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0 }, inconnue: 4 }, population: { ...RESULTAT.population, classes_connues: 0 }, modes: [] })],
		// Counts that do not add up to the served known classes: the sentence would contradict the bars.
		// A missing class key is not a zero.
		['missing class key', avec({ distribution: { classes: { A: 40, B: 120, C: 610, D: 1071, E: 1131, F: 484 } as ResultatDpeIndicateur['distribution']['classes'], inconnue: 56 } })],
		['inconsistent counts', avec({ population: { ...RESULTAT.population, classes_connues: 3000 } })]
	])('%s → null', (_cas, commune) => {
		expect(repartitionDpe(commune)).toBeNull();
	});
});
