import { describe, expect, it } from 'vitest';
import { panneauDeTheme } from './__fixtures__/fiche-commune-rendue';
import type { FicheGrid as Grid, FicheGridCell } from '$lib/types/fiche-grid';
import { mapCommuneToGrid } from './commune-grid';
import { COMMUNE_INDICATORS_44, COMMUNE_INDICATORS_85 } from './__fixtures__/commune-comparison-indicators';
import { enrichCommuneIndicatorPresentation } from './commune-indicator-presentation';

function cell(grid: Grid, conceptId: string): FicheGridCell {
	const found = grid.rows.find((row) => row.concept.id === conceptId)?.cells[grid.columns[0].id];
	if (!found) throw new Error(`Missing test cell: ${conceptId}`);
	return found;
}

function withoutCriticalSummary(value: FicheGridCell): Omit<FicheGridCell, 'criticalSummary'> {
	const { criticalSummary: _summary, ...rest } = value;
	return rest;
}

describe('commune indicator critical presentation', () => {
	it('projects typed source and window metadata without parsing reservations', () => {
		const mapped = mapCommuneToGrid(COMMUNE_INDICATORS_44);
		const current = cell(mapped.themes.marche, 'commune-marche:dvf-maison-mediane-courante');
		const variation = cell(mapped.themes.marche, 'commune-marche:dvf-maison-variation');
		const dpe = cell(mapped.themes.energie, 'commune-energie:dpe-mediane');
		expect(current).toMatchObject({ source: { nom: 'Source DVF 44', url: 'reference-dvf 44' }, millesime: '2025-01-01 → 2026-01-01 (fin exclue)', estDatable: true });
		expect(variation.millesime).toBe('2024-01-01 → 2025-01-01 (fin exclue) / 2025-01-01 → 2026-01-01 (fin exclue)');
		expect(dpe).toMatchObject({ source: { nom: 'Source DPE 44', url: 'reference-dpe 44' }, millesime: 'Évaluation : 2026-08-05', estDatable: true });
		expect(cell(mapped.themes.marche, 'commune-marche:dvf-maison-effectif-precedent').millesime).toBe('2024-01-01 → 2025-01-01 (fin exclue)');
		expect(cell(mapped.themes.marche, 'commune-marche:context:dvf-ancrage')).toMatchObject({ millesime: null, estDatable: false });
		const absent = mapCommuneToGrid({ ...COMMUNE_INDICATORS_44, indicateurs_dvf: { etat: 'not_qualified', motif: 'Bloc indisponible.' } });
		expect(cell(absent.themes.marche, 'commune-marche:dvf-indicateurs-absence')).toMatchObject({ source: null, millesime: null });
	});
	it('uses received anchor bounds and keeps all distinct source names', () => {
		const dvf = structuredClone(COMMUNE_INDICATORS_44.indicateurs_dvf!);
		if (!('version_contrat' in dvf)) throw new Error('Expected DVF value block');
		dvf.provenance.push({ ...dvf.provenance[0], nom: 'Source DVF complémentaire', reference: 'other-reference' });
		dvf.ancrage_temporel = { etat: 'delivery_received_and_stock_bound', periode_debut_inclus: '2024-01-01', periode_fin_exclue: '2026-01-01' } as never;
		const grid = mapCommuneToGrid({ ...COMMUNE_INDICATORS_44, indicateurs_dvf: dvf }).themes.marche;
		expect(cell(grid, 'commune-marche:context:dvf-ancrage')).toMatchObject({
			millesime: '2024-01-01 → 2026-01-01 (fin exclue)',
			source: { nom: 'Source DVF 44 · Source DVF complémentaire', url: null }
		});
	});
	it('classifies previous-window metadata without hiding its period, count or unknown coverage', () => {
		const grid = mapCommuneToGrid(COMMUNE_INDICATORS_44).themes.marche;
		const target = cell(grid, 'commune-marche:dvf-maison-variation');
		expect(target.criticalSummary?.warnings.join(' ')).not.toContain('Fenêtre précédente — Génération');
		expect(target.criticalSummary?.warnings.join(' ')).not.toContain('Fenêtre précédente — Snapshot');
		expect(target.criticalSummary?.warnings).toContain('Fenêtre précédente — Couverture : unknown');
		expect(target.criticalSummary?.facts).toContain('Fenêtre précédente — 18 mutations');
		expect(target.criticalSummary?.facts).toContain('Fenêtre précédente — Période : 2024-01-01 → 2025-01-01 (fin exclue)');
		expect(target.reserves).toContain('Fenêtre précédente — Génération : generation-dvf-44');
	});
	it.each([
		['Validité — inconnue [Validité unknown : 1\u202f005] : 1\u202f005', 'Validité — inconnue : 1\u202f005'],
		['Validité métier future : situation à vérifier', 'Validité métier future : situation à vérifier']
	])('does not hide grouped unknown counts or a new structured reservation: %s', (reserve, warning) => {
		const source = mapCommuneToGrid(COMMUNE_INDICATORS_44).themes.energie;
		const id = 'commune-energie:dpe-part-fg';
		const column = source.columns[0].id;
		const target = cell(source, id);
		const grid = enrichCommuneIndicatorPresentation({ ...source, rows: source.rows.map(row => row.concept.id === id
			? { ...row, cells: { ...row.cells, [column]: { ...target, reserves: [...target.reserves, reserve] } } }
			: row) });
		expect(cell(grid, id).criticalSummary?.warnings).toContain(warning);
		expect(cell(grid, id).reserves).toContain(reserve);
	});
	it('enriches only additive Commune indicator cells without mutating their contract', () => {
		const integrated = mapCommuneToGrid(COMMUNE_INDICATORS_44).themes.marche;
		const source = {
			...integrated,
			rows: integrated.rows.map((row) => ({ ...row, cells: Object.fromEntries(Object.entries(row.cells).map(([id, value]) => [id, withoutCriticalSummary(value)])) }))
		} as Grid;
		const enriched = enrichCommuneIndicatorPresentation(source);

		expect(enriched).not.toBe(source);
		expect(enriched.columns).toBe(source.columns);
		for (const [index, row] of source.rows.entries()) {
			const projectedRow = enriched.rows[index];
			expect(projectedRow.concept).toBe(row.concept);
			const sourceCell = row.cells[source.columns[0].id];
			const projectedCell = projectedRow.cells[source.columns[0].id];
			if (/^commune-marche:(?:context:)?dvf-/.test(row.concept.id)) {
				expect(projectedCell).not.toBe(sourceCell);
				expect(projectedCell.details).toBe(sourceCell.details);
				expect(projectedCell.reserves).toBe(sourceCell.reserves);
				expect(withoutCriticalSummary(projectedCell)).toStrictEqual(sourceCell);
				expect(projectedCell.criticalSummary).toBeDefined();
			} else {
				expect(projectedCell).toBe(sourceCell);
			}
		}
		expect(source.rows
			.filter((row) => /^commune-marche:(?:context:)?dvf-/.test(row.concept.id))
			.every((row) => cell(source, row.concept.id).criticalSummary === undefined)).toBe(true);
	});

	// The value, window, count, zero and qualified-absence rules are read on the theme the commune
	// page shows; rows are keyed by line, the two windows sharing a count field id.
	it('keeps the current house median, its window and its count visible', () => {
		const container = panneauDeTheme(COMMUNE_INDICATORS_44, 'immobilier');
		const median = container.querySelector('[data-ligne="dvf-maison-mediane-courante"]')!;
		const count = container.querySelector('[data-ligne="dvf-maison-effectif-courant"]')!;

		expect(median.querySelector('.state-value .value')?.textContent).toBe('2 500');
		expect(median.querySelector('.state-value .unit')?.textContent?.trim()).toBe('€/m²');
		expect(median.querySelector('.tab-note-texte')).toHaveTextContent('2025-01-01 → 2026-01-01 (fin exclue)');
		expect(count.querySelector('.state-value .value')?.textContent).toBe('20');
		expect(count.querySelector('.tab-note-texte')).toHaveTextContent('2025-01-01 → 2026-01-01 (fin exclue)');
	});

	it('renders a zero DPE F-G share as a value, with its evaluation date and population', () => {
		const container = panneauDeTheme(COMMUNE_INDICATORS_44, 'energie-bati');
		const share = container.querySelector('[data-ligne="dpe-part-fg"]')!;

		expect(share.querySelector('[data-state="value"] .value')?.textContent).toBe('0');
		expect(share.querySelector('[data-state="value"] .unit')?.textContent?.trim()).toBe('%');
		expect(share.querySelector('.tab-note-texte')).toHaveTextContent('Évaluation : 2026-08-05');
		expect(container.querySelector('[data-field-id="dpe.indicateurs.population.total"] dd'))
			.toHaveTextContent('5 diagnostics disponibles');
	});

	it('keeps a qualified absence visible instead of a value', () => {
		const container = panneauDeTheme(COMMUNE_INDICATORS_85, 'immobilier');
		const previous = container.querySelector('[data-ligne="dvf-maison-mediane-precedente"]')!;

		expect(previous.querySelector('.state-value')).toBeNull();
		expect(previous.querySelector('[data-state="insufficient_sample"] .state-badge')?.textContent).toBe('Effectif insuffisant');
		expect(previous.querySelector('.state-help')).toHaveTextContent('Trop peu d\'observations (9) pour publier une valeur.');
	});

	it.each(['Couverture : unknown', 'Couverture : partial', 'Source : inconnue'])(
		'keeps critical technical unknown visible: %s', (reserve) => {
		const source = mapCommuneToGrid(COMMUNE_INDICATORS_44).themes.marche;
		const targetId = 'commune-marche:dvf-maison-mediane-courante';
		const target = cell(source, targetId);
		const columnId = source.columns[0].id;
		const enriched = enrichCommuneIndicatorPresentation({
			...source,
			rows: source.rows.map((row) => row.concept.id === targetId
				? { ...row, cells: { ...row.cells, [columnId]: { ...target, reserves: [...target.reserves, reserve] } } }
				: row)
		});
		expect(cell(enriched, targetId).criticalSummary?.warnings).toContain(reserve);
	});

	it('keeps non-problematic translated DPE axes in details instead of the visible warning summary', () => {
		const grid = mapCommuneToGrid(COMMUNE_INDICATORS_44).themes.energie;
		const summary = cell(grid, 'commune-energie:dpe-part-fg').criticalSummary!;
		expect(summary.warnings.join(' ')).not.toContain('not_expired_by_date');
		expect(summary.warnings.join(' ')).not.toContain('non expirée à la date');
		expect(cell(grid, 'commune-energie:dpe-part-fg').reserves.join(' ')).toContain('Validité not_expired_by_date : 4');
	});

	it('shows consequential DPE axes in French while retaining raw enum metadata only in details', () => {
		const grid = mapCommuneToGrid(COMMUNE_INDICATORS_44).themes.energie;
		const target = cell(grid, 'commune-energie:dpe-part-fg');
		const warnings = target.criticalSummary!.warnings.join(' ');
		expect(warnings).toContain('Remplacement — non vérifié : 5');
		expect(warnings).not.toContain('not_checked');
		expect(target.reserves.join(' ')).toContain('Remplacement not_checked : 5');
	});
});
