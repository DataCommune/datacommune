import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import { panneauDeTheme } from './__fixtures__/fiche-commune-rendue';
import { downloadCsv } from '$lib/utils/csv-download';
import { ficheDepth, mapCommuneToFiche } from './commune-fiche';
import { millesimesFromCommune } from './explications';
import fixture from './__fixtures__/commune-cor14-api.json';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

// Read on the theme panel of the commune page (04.8-09, D-20). The capture predates
// D-47: the unknown count of 85991 is declared as the API serves it since 03-06 (not_reported),
// never left undeclared — an undeclared absence is not rendered at all.
const DECLARE_85991 = { nb_equipements: { etat: 'not_reported', motif: 'Aucune ligne de la source pour cette commune.' } };

describe('COR-14 real PostGIS HTTP payload -> mapper -> theme and CSV', () => {
	it.each(['44991', '85991'] as const)('keeps source zero distinct from unknown coverage for %s', (code) => {
		const commune = fixture[code].donnees[0] as CommuneSynthese;
		const known = code === '44991';
		const themes = mapCommuneToFiche(commune).themes;
		const theme = themes.find(t => t.key === 'environnement')!;
		const row = theme.rows.find(r => r.fieldId === 'equipements.nb_equipements')!;
		const millesimes = millesimesFromCommune(commune);
		expect(millesimes.equipements).toBe(known ? '2022' : null);
		if (known) {
			expect(row.value).toBe('0');
			expect(row.state).toBe('value');
			expect(row.available).not.toBe(false);
		} else {
			expect(row.value).not.toBe('0');
			expect(row.state).not.toBe('value');
			expect(row.available === false || row.muted === true).toBe(true);
		}
		const served = (known ? commune : { ...commune, etats: DECLARE_85991 }) as CommuneSynthese;
		const container = panneauDeTheme(served, 'services-transports');
		const renderedRow = container.querySelector('[data-field-id="equipements.nb_equipements"]')!;
		expect(renderedRow.querySelector('dt')?.textContent).toContain('Effectif d’équipements observé');
		if (known) {
			// A measured zero is a value, with its vintage.
			expect(renderedRow.querySelector(':scope > dd .state-value .value')?.textContent).toBe('0');
			expect(renderedRow.querySelector('.tab-note-texte')?.textContent).toBe('Millésime 2022');
		} else {
			// The unknown count is its declared state, never a 0.
			expect(renderedRow.querySelector(':scope > dd .state-value')).toBeNull();
			expect(renderedRow.querySelector(':scope > dd .state-badge')?.textContent).toBe('Non renseigné');
		}
		expect(container.textContent).toContain(known ? 'Équipements : millésime 2022' : 'Millésime des équipements inconnu');
		// The honesty caveat of the counts is a note of the theme (« Portée des dénombrements »), not a
		// caveat of the row: it stays visible under the rows, outside any panel, while the row itself
		// carries no caveat under its value (03-17 R-1).
		const horsVolet = (element: Element | null) => {
			const copie = element?.cloneNode(true) as Element | undefined;
			copie?.querySelectorAll('dialog').forEach((dialog) => dialog.remove());
			return copie?.textContent ?? '';
		};
		expect(horsVolet(renderedRow.querySelector('dd'))).not.toContain('sans garantie d’exhaustivité');
		const notes = [...container.querySelectorAll('.theme-note')].map((note) => note.textContent).join(' ');
		expect(notes).toContain('sans garantie d’exhaustivité');
		expect(notes).toContain('Une donnée absente ne vaut pas zéro.');
		expect(ficheDepth(themes)).toEqual(ficheDepth(themes.map(t => ({
			...t, rows: t.rows.filter(r => r.kind !== 'context')
		}))));
	});

	it.each(['44991', '85991'] as const)('exports the same source boundary and local year for %s', (code) => {
		const commune = fixture[code].donnees[0] as CommuneSynthese;
		const theme = mapCommuneToFiche(commune).themes.find(t => t.key === 'environnement')!;
		let csv = '';
		vi.stubGlobal('Blob', class { constructor(parts: string[]) { csv = parts.join(''); } });
		vi.stubGlobal('URL', { createObjectURL: () => 'blob:fixture', revokeObjectURL: () => {} });
		vi.spyOn(document, 'createElement').mockReturnValue({ click: () => {} } as HTMLAnchorElement);
		downloadCsv(['Indicateur', 'Valeur'], theme.rows.filter(r => r.available !== false).map(r => [r.label, r.value]), 'commune.csv');
		expect(csv).toContain('sans garantie d’exhaustivité');
		expect(csv).toContain('Une donnée absente ne vaut pas zéro.');
		if (code === '44991') {
			expect(csv).toContain('Effectif d’équipements observé,0');
			expect(csv).toContain('millésime 2022');
		} else {
			expect(csv).not.toContain('Effectif d’équipements observé,0');
			expect(csv).toContain('Millésime des équipements inconnu');
		}
	});
});
