import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import { panneauDeTheme } from './__fixtures__/fiche-commune-rendue';
import { downloadCsv } from '$lib/utils/csv-download';
import { ficheDepth, mapCommuneToFiche } from './commune-fiche';
import { contexteLoyer } from './loyers-prediction';
import fixture from './__fixtures__/loyers-cor06-api.json';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('COR-06 official source -> PostGIS -> HTTP fixture -> rendered context', () => {
	const commune = fixture.communes['05066'].donnees[0] as CommuneSynthese;
	it('does not count source context as additional indicators', () => {
		const themes = mapCommuneToFiche(commune).themes;
		const metricsOnly = themes.map(theme => ({ ...theme, rows: theme.rows.filter(row => row.kind !== 'context') }));
		expect(ficheDepth(themes)).toEqual(ficheDepth(metricsOnly));
		for (const row of themes.flatMap(theme => theme.rows)) {
			if (row.kind === 'context') expect(row.fieldId).toBeUndefined();
			else { expect(row.fieldId).toBeTruthy(); expect(row.state).toBeTruthy(); }
		}
	});
	it('renders charges, reference good, quarter and true mesh quality, preserving zero observations', () => {
		const model = mapCommuneToFiche(commune);
		expect(model.kpi[0].label).toBe("Loyer d'annonce prédit (appartement)");
		expect(model.kpi[0].unit).toBe('€/m²/mois CC');
		// The rent and its context are read on the Immobilier panel of the commune page.
		const container = panneauDeTheme(commune, 'immobilier');
		const rent = container.querySelector('[data-field-id="loyers.loyer_m2_appartement"]')!;
		expect(rent.querySelector(':scope > dd .state-value')).not.toBeNull();
		const text = container.textContent ?? '';
		for (const expected of ['T3 2025', '52 m²', '22,2 m²/pièce', 'non meublé', 'charges comprises', 'Maille, zone 1', '0 obs. commune / 484 obs. maille', '95 %', '7,58–12,56', 'R² ajusté 0,777', 'prudence']) {
			expect(text).toContain(expected);
		}
		expect(text).not.toContain('Loyer médian');
		expect(text).not.toContain('hors charges');
	});
	it('renders an explicitly synthetic EPCI scope without relabeling it commune', () => {
		const synthetic = { ...commune.loyers_appartement!, type_prediction: 'epci' };
		const rows = contexteLoyer('appartement', synthetic, synthetic.loyer_m2);
		expect(rows[1].value).toContain('EPCI, zone 1');
		expect(rows[1].value).not.toContain('Commune / arrondissement');
	});
	it('keeps historical metadata unknown without assigning T3 2025 or reference dimensions', () => {
		const legacy = { ...commune, annee_loyers: 2023, loyers_appartement: null };
		const theme = mapCommuneToFiche(legacy).themes[0];
		const rows = theme.rows.filter(r => /Contexte loyer/.test(r.label));
		expect(rows).toHaveLength(1);
		expect(rows[0].value).toContain('Précisions inconnues');
		expect(rows[0].value).not.toMatch(/T3|2025|52 m²/);
		expect(rows[0].value).not.toContain('Réimport');
	});
	it.each([false, true])('exports the context and reserves through CSV (legacy=%s)', (legacy) => {
		const c = legacy ? { ...commune, loyers_appartement: null } : commune;
		const theme = mapCommuneToFiche(c).themes[0];
		let csv = '';
		vi.stubGlobal('Blob', class { constructor(parts: string[]) { csv = parts.join(''); } });
		vi.stubGlobal('URL', { createObjectURL: () => 'blob:fixture', revokeObjectURL: () => {} });
		vi.spyOn(document, 'createElement').mockReturnValue({ click: () => {} } as HTMLAnchorElement);
		// Same row policy as the public Commune page's exportTheme.
		downloadCsv(['Indicateur', 'Valeur'], theme.rows.filter(r => r.available !== false).map(r => [r.label, r.value]), 'loyers.csv');
		expect(csv).toContain(legacy ? 'Précisions inconnues' : '0 obs. commune / 484 obs. maille');
		if (!legacy) expect(csv).toContain('Aucune évolution inter-cartes déduite');
	});
});
