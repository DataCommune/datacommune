import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { TendancePrix } from '$lib/api/commune';
import { communeAvecTendance, TENDANCE_EVOLUTION, TENDANCE_INSUFFISANTE, TENDANCE_POINTS } from '$lib/data/__fixtures__/commune-tendance';
import { communeKpis, reperesConnus } from '$lib/data/commune-kpi';
import Kpi from './Kpi.svelte';

// The tile « Tendance du prix médian » in its three states (decision of Ianis of 2026-11-11): a served
// change with its curve and period; points without a percentage; no tile under two medians. The
// medians and counts of every year are in the panel, never on the face of the tile.

const texte = (element: Element | null) => element?.textContent?.replace(/\s+/g, ' ').trim() ?? null;

function rendre(tendance: TendancePrix) {
	const kpi = reperesConnus(communeKpis(communeAvecTendance(tendance))).find((item) => item.id === 'tendance')!;
	const rendu = render(Kpi, { kpi, forme: 'detail' });
	const lignes = [...rendu.container.querySelectorAll('.kpi-line')];
	const volet = rendu.container.querySelector('dialog')!;
	const face = texte(rendu.container.querySelector('.cle'))!.replace(texte(volet)!, '');
	return { ...rendu, lignes, volet, face };
}

describe('Kpi — tendance du prix, série annuelle', () => {
	it('état « évolution » : pourcentage, courbe de cinq points reliés, période calculée', () => {
		const { lignes, face, getAllByRole } = rendre(TENDANCE_EVOLUTION);
		expect(lignes.map((ligne) => texte(ligne.querySelector('.kpi-sub')))).toEqual(['Appartements', 'Maisons']);
		const [appartements] = lignes;
		expect(texte(appartements.querySelector('.state-value'))).toBe('+12 %');
		expect(appartements.querySelector('.state-value')?.getAttribute('data-state')).toBe('value');
		expect(texte(appartements.querySelector('[data-periode]'))).toBe('2021 → 2025');
		const courbe = appartements.querySelector('svg[data-courbe]')!;
		expect([...courbe.querySelectorAll('circle')].map((point) => point.getAttribute('data-annee'))).toEqual(['2021', '2022', '2023', '2024', '2025']);
		expect(courbe.querySelectorAll('line')).toHaveLength(4);
		expect(courbe.getAttribute('aria-label')?.replace(/[  ]/g, ' ')).toBe('Prix médian au m² par année : 3 500 €/m² en 2021, 3 920 €/m² en 2025.');
		// A rising series goes up on the screen: the last point is higher than the first.
		const y = [...courbe.querySelectorAll('circle')].map((point) => Number(point.getAttribute('cy')));
		expect(y.at(-1)!).toBeLessThan(y[0]);
		// One information button for the tile; the yearly figures are not on its face.
		expect(getAllByRole('button', { name: 'Détail : Tendance du prix médian' })).toHaveLength(1);
		expect(face).not.toContain('3 996');
		expect(face).not.toContain('année par année');
	});

	it('état « points » : la courbe sans pourcentage, un trou là où une année n’a pas de médiane', () => {
		const { lignes, volet } = rendre(TENDANCE_POINTS);
		expect(lignes).toHaveLength(1);
		const [maisons] = lignes;
		expect(maisons.querySelector('.state-value')).toBeNull();
		expect(maisons.querySelector('.state-absence')?.getAttribute('data-state')).toBe('insufficient_sample');
		expect(texte(maisons.querySelector('.state-badge'))).toBe('Évolution non chiffrée');
		expect(texte(maisons)).not.toMatch(/\d\s?%/);
		const courbe = maisons.querySelector('svg[data-courbe]')!;
		expect([...courbe.querySelectorAll('circle')].map((point) => point.getAttribute('data-annee'))).toEqual(['2021', '2022', '2024', '2025']);
		// 2021-2022 and 2024-2025 are joined; nothing is drawn across the missing 2023.
		expect(courbe.querySelectorAll('line')).toHaveLength(2);
		expect(texte(maisons.querySelector('[data-periode]'))).toBe('2021 → 2025');
		expect(texte(volet)?.replace(/[  ]/g, ' ')).toContain('Trop peu de ventes pour chiffrer une évolution : il en faut au moins 30 en 2021 et en 2025.');
	});

	it('état « effectif insuffisant » : aucune tuile dans « L’essentiel », comme toute tuile sans valeur', () => {
		const tous = communeKpis(communeAvecTendance(TENDANCE_INSUFFISANTE));
		expect(tous.map((item) => item.id)).toContain('tendance');
		expect(reperesConnus(tous).map((item) => item.id)).not.toContain('tendance');
	});
});
