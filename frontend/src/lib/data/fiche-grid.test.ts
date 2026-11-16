import { describe, it, expect } from 'vitest';
import { mapCommuneToFiche } from './commune-fiche';
import { mapBienToFiche } from './bien-fiche';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { mockResolve85 } from './__fixtures__/resolve-85';
import { millesimesFromCommune } from './explications';
import { formatField } from './formatters';
import { formatPriceBucket } from './dvf-minimisation';
import baseline from './__fixtures__/fiche-grid-baseline.json';
import { rowsToGrid, kpisToGrid, gridIndicatorCount } from './fiche-grid';
import type { FicheGridColumn, FicheGridCell, FicheGridOptions } from '$lib/types/fiche-grid';
import type { KvRow, KpiItem, QualifiedAbsenceReason, FieldValue } from '$lib/types/fiche';
import type { DatasetProvenance } from '$lib/api/resolve';
import { EXPLICATIONS_ETATS, LIBELLES_ECARTS_HISTORIQUES as LIBELLES_ETATS } from './etats-libelles';
import { GOLDEN_REAL_CORPUS } from './__fixtures__/golden-cases';
import { deriveDpeAttributionView } from './dpe-attribution-view';

const column: FicheGridColumn = { id: '44109', entity: 'commune', scope: 'commune', grain: 'Commune', label: 'Nantes', code: '44109' };
const options = { id: 'test-table', millesimes: { revenus: '2021' }, sources: { revenus: { nom: 'Filosofi (Revenus et Pauvrete)', url: 'https://www.insee.fr/fr/statistiques/7756855' } } };
function one(row: KvRow, selected = column, metadata: FicheGridOptions = options): FicheGridCell {
	return rowsToGrid([row], selected, metadata).rows[0].cells[selected.id];
}
const syntheticProvenance: DatasetProvenance = {
	dataset: 'revenus', nom: 'Source resolve synthétique', url: 'https://example.org/source', licence: 'Test', producteur: 'Test',
	periode: { statut: 'connue', label: '2026', debut: null, fin: null }, derniere_importation_reussie: null,
	couverture: { type: 'inconnue', departements: null }, cadence: { statut: 'non_publiee_ou_irreguliere', label: null, evidence_url: null, intervalle_jours: null }, etat: 'inconnu'
};

// Drop only additive metadata (presentation, 03-09 valuePath); every historical property stays asserted.
const ADDITIVE = new Set(['presentation', 'valuePath']);
function historical(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(historical);
	if (value && typeof value === 'object') return Object.fromEntries(
		Object.entries(value).filter(([key]) => !ADDITIVE.has(key)).map(([key, entry]) => [key, historical(entry)])
	);
	return value;
}

describe('bounded historical baseline, captured before grid implementation', () => {
	it('preserves every synthetic Commune/Bien KPI and theme row', () => {
		expect(historical(mapCommuneToFiche(COMMUNE_85047_CHALLANS))).toEqual(baseline.synthetic.commune);
		expect(historical(mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS))).toEqual(baseline.synthetic.bien);
		expect(millesimesFromCommune(COMMUNE_85047_CHALLANS)).toEqual(baseline.synthetic.millesimes);
	});
	it.each(['value', 'unit', 'fieldId'] as const)('rejects a corrupted historical %s', (key) => {
		const changed = structuredClone(baseline.synthetic.commune);
		Object.assign(changed.kpi[0], { [key]: 'CORRUPTED' });
		expect(() => expect(changed).toEqual(baseline.synthetic.commune)).toThrow();
	});
	it('rejects a lost context reservation', () => {
		const changed = structuredClone(baseline.synthetic.commune);
		const theme = changed.themes.find(t => t.rows.some(r => 'kind' in r && r.kind === 'context'))!;
		theme.rows = theme.rows.filter(r => !('kind' in r && r.kind === 'context'));
		expect(() => expect(changed).toEqual(baseline.synthetic.commune)).toThrow();
	});
	it('freezes synthetic zero, absence, error and masked price without a numeric guess', () => {
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 0 }).value).toBe('0 %');
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'out_of_coverage' }).value).toBe('non disponible');
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'temporary_error' }).state).toBe('temporary_error');
		expect(formatPriceBucket('bien.dvf.prix_m2', 2154, 500)).toBe('2 000 – 2 500 €/m²');
		expect(baseline.synthetic.bien.kpi.some(item => item.value === '85047000AB0042')).toBe(true);
	});
	it('keeps the frozen official income observation distinct from synthetic values', () => {
		const official = baseline.officialFrozen.observations[0];
		expect(formatField(official.fieldId!, 'Revenu déclaré médian', { state: 'value', value: official.raw }).value).toBe(official.display);
		expect(baseline.officialFrozen.observations[1].reservation).toContain('distincte du KPI');
	});
});

describe('qualified grid transport', () => {
	it('transports the frozen official income display, unit, source and table vintage', () => {
		const official = baseline.officialFrozen.observations[0];
		const cell = one(formatField(official.fieldId!, 'Revenu déclaré médian', { state: 'value', value: official.raw }));
		expect(cell).toMatchObject({ kind: 'value', display: official.display, unit: official.unit, grain: official.grain, millesime: official.year, source: { nom: official.source } });
		for (const key of ['display', 'unit', 'grain', 'millesime', 'source']) {
			expect(() => expect({ ...cell, [key]: 'CORRUPTED' }).toEqual(cell)).toThrow();
		}
	});
	it('uses the table scope, while source precedence remains self-keyed', () => {
		const row = formatField('revenus.revenu_median', 'Revenu', { state: 'value', value: 24380 });
		const metadata = { ...options, provenances: { revenus: syntheticProvenance } };
		expect(one(row, column, metadata)).toMatchObject({ millesime: '2021', source: { nom: 'Source resolve synthétique' } });
		const bien = { ...column, id: 'bien', entity: 'bien', scope: 'bien', grain: 'Logement' } as const;
		expect(one(row, bien, metadata).millesime).toBe('2026');
		expect(one(row, column, { ...metadata, millesimes: {} }).millesime).toBeNull();
		expect(() => expect(one(row, bien, metadata)).toEqual(one(row, column, metadata))).toThrow();
	});
	it('leaves unknown provenance unknown', () => {
		expect(rowsToGrid([formatField('revenus.revenu_median', 'Revenu', { state: 'value', value: 1 })], column, { id: 'unknown' }).rows[0].cells[column.id]).toMatchObject({ source: null, millesime: null });
	});
	it.each(['not_reported', 'out_of_coverage', 'temporary_error', 'not_applicable'] as const)('keeps %s absent without any value', state => {
		const cell = one({ label: 'Synthetic absence', value: '999', state });
		expect(cell).toMatchObject({ kind: 'absent', state, label: LIBELLES_ETATS[state], explanation: EXPLICATIONS_ETATS[state] });
		expect(cell).not.toHaveProperty('display');
		expect(cell).not.toHaveProperty('value');
		expect(cell).not.toHaveProperty('measuredZero');
		expect(cell).toHaveProperty('reason', undefined);
	});
	it('distinguishes zero measured, rounded zero and masked textual zero', () => {
		expect(one(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 0 }))).toMatchObject({ kind: 'value', measuredZero: true, display: '0 %' });
		expect(one(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 0.0001 }))).toMatchObject({ measuredZero: false, display: '0 %' });
		expect(one({ label: 'Synthetic bucket', value: '0 – 500 €', state: 'value' })).not.toHaveProperty('measuredZero', true);
	});
	it('keeps a masked DVF bracket and its parcel reservation as strings', () => {
		const item: KpiItem = { label: 'Prix au m² sur la parcelle', fieldId: 'bien.dvf.prix_m2', value: '2 000 – 2 500 €/m²', verdict: 'info', presentation: { state: 'value', grain: 'Parcelle', reserves: ['Non attribuée'] } };
		const cell = kpisToGrid([item], { ...column, scope: 'bien', entity: 'bien', grain: 'Logement' }, options).rows[0].cells[column.id];
		expect(cell).toMatchObject({ kind: 'value', display: '2 000 – 2 500 €/m²', grain: 'Parcelle', reserves: ['Non attribuée'] });
		expect(() => expect({ ...cell, reserves: [] }).toEqual(cell)).toThrow();
	});
	it('keeps the existing KPI definition, numeric gap, tolerance and reference', () => {
		const item = mapCommuneToFiche(COMMUNE_85047_CHALLANS).kpi.find(item => item.fieldId === 'revenus.revenu_median')!;
		const cell = kpisToGrid([{ ...item, presentation: { state: 'value' } }], column, options).rows[0].cells[column.id];
		expect(cell.details.definition).toContain('revenu');
		expect(cell.details.ecart).toBe(item.ecartTexte);
		expect(cell.details.reference).not.toBeNull();
		expect(cell.details.tolerance).toContain('±');
		const noGap = kpisToGrid([{ ...item, ecartTexte: null, presentation: { state: 'value' } }], column, options).rows[0].cells[column.id];
		expect(noGap.details.tolerance).toBeNull();
	});
	it('accepts a second synthetic entity column without calculating comparisons', () => {
		const first = rowsToGrid([formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 0 })], column, options);
		const secondColumn = { ...column, id: 'synthetic-second', label: 'Commune fictive', code: '00000' };
		const second = rowsToGrid([formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 9 })], secondColumn, options);
		const grid = { ...first, columns: [...first.columns, ...second.columns], rows: [{ ...first.rows[0], cells: { ...first.rows[0].cells, ...second.rows[0].cells } }] };
		expect(Object.keys(grid.rows[0].cells)).toEqual(['44109', 'synthetic-second']);
		expect(grid.rows[0].cells['synthetic-second']).toMatchObject({ display: '9 %', details: { ecart: null } });
		expect(gridIndicatorCount(grid)).toBe(1);
	});
});

describe('integration invariants and explicitly synthetic qualified reasons', () => {
	it.each([
		{ code: 'zero_denominator', label: 'Taux non calculable', explanation: 'La population de référence vaut 0.', evidence: 'Synthetic explicit denominator = 0' },
		{ code: 'insufficient_sample', label: 'Valeur non publiée', explanation: 'Effectif insuffisant indiqué par la source.', evidence: 'Synthetic source decision, no inferred threshold', effectif: 3 },
		{ code: 'suppressed', label: 'Valeur masquée', explanation: 'Valeur non diffusée par la source.', evidence: 'Synthetic explicit disclosure decision' },
		{ code: 'source_reason', label: 'Non attribuée', explanation: 'Aucune identité du logement prouvée.', evidence: 'Synthetic public attribution reason' }
	] satisfies QualifiedAbsenceReason[])('preserves the supplied $code reason and its evidence', reason => {
		const cell = one({ label: 'Synthetic qualified absence', value: 'n.d.', presentation: { state: 'not_reported', reason } });
		expect(cell).toMatchObject({ kind: 'absent', state: 'not_reported', reason });
		expect(cell).not.toHaveProperty('display');
		expect(cell).not.toHaveProperty('value');
	});
	it('never guesses a NULL cause, even when another synthetic field reports zero', () => {
		const grid = rowsToGrid([
			{ id: 'numerator', label: 'Numérateur', value: '0', presentation: { state: 'value', measuredZero: true } },
			{ id: 'rate', label: 'Taux', value: 'n.d.', presentation: { state: 'not_reported' } }
		], column, options);
		expect(grid.rows[1].cells[column.id]).toMatchObject({ kind: 'absent', reason: undefined });
	});
	it('requires source qualification instead of interpreting historical KPI text', () => {
		expect(() => kpisToGrid([{ label: 'Unknown KPI', value: 'n.d.', verdict: 'info' }], column, options)).toThrow('Unqualified grid cell');
		expect(() => rowsToGrid([{ label: 'Unknown row', value: '0 %' }], column, options)).toThrow('Unqualified grid cell');
		expect(() => one({ label: 'Conflict', value: '0', state: 'temporary_error', presentation: { state: 'value' } })).toThrow('Conflicting grid states');
	});
	it('never looks up an identifier or context label in the field registry', () => {
		const text = '85047000AB0042-IDENTIFIANT-TRES-LONG-000000000000000000000';
		const grid = rowsToGrid([
			{ id: 'not-in-registry', label: 'Identifiant', value: text, presentation: { state: 'value' } },
			{ id: 'reservation', kind: 'context', label: 'not-a-field-id', value: 'Réserve source', muted: true }
		], column, options);
		expect(grid.rows[0].concept).toMatchObject({ id: 'test-table:not-in-registry', fieldId: undefined, definition: null, unit: null });
		expect(grid.rows[0].cells[column.id]).toMatchObject({ kind: 'value', display: text, estDatable: false, source: null, millesime: null });
		expect(grid.rows[1].cells[column.id]).toMatchObject({ kind: 'context', text: 'Réserve source' });
		expect(gridIndicatorCount(grid)).toBe(1);
	});
	it('keeps concept keys stable across order and value changes; rejects duplicates', () => {
		const a: KvRow = { label: 'A', value: '1', state: 'value' };
		const b: KvRow = { label: 'B', value: '2', state: 'value' };
		const first = rowsToGrid([a, b], column, options);
		const second = rowsToGrid([b, { ...a, value: '99' }], column, options);
		expect(first.rows[0].concept.id).toBe(second.rows[1].concept.id);
		expect(() => rowsToGrid([a, a], column, options)).toThrow('Duplicate grid concept');
	});
	it('preserves all historical context rows as non-indicators', () => {
		for (const theme of mapCommuneToFiche(COMMUNE_85047_CHALLANS).themes) {
			const rows = theme.rows.filter(row => row.kind === 'context' || row.state != null);
			const grid = rowsToGrid(rows, column, { ...options, id: theme.key });
			expect(gridIndicatorCount(grid)).toBe(rows.filter(row => row.kind !== 'context').length);
			rows.forEach((row, index) => {
				if (row.kind === 'context') expect(grid.rows[index].cells[column.id]).toMatchObject({ kind: 'context', text: row.value });
			});
		}
	});
	it('keeps the approved DPE inventory-presence invariant separate from dwelling attribution', () => {
		const carrier = GOLDEN_REAL_CORPUS.find(item => item.case_id === 'golden-address-001')!;
		expect(carrier.invariants.dpe).toMatchObject({ dpe_inventory_presence: 'present', dpe_primary_diagnostic_present: false });
		if (carrier.type !== 'address') throw new Error('Expected approved address carrier');
		// Its public invariant is frozen; its mapper values are synthetic by fixture contract.
		const selection = deriveDpeAttributionView(carrier.resolve.dpe_attribution, { hrefBase: '/bien/test' }).ficheSelection;
		expect(selection.diagnostic).toBeNull();
		const fiche = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, selection);
		const rows = fiche.themes.flatMap(theme => theme.rowsBien ?? []).filter(row => row.fieldId?.startsWith('bien.dpe.'));
		expect(rows.length).toBeGreaterThan(0);
		const grid = rowsToGrid(rows, { ...column, scope: 'bien', grain: 'Logement', entity: 'bien' }, options);
		expect(grid.rows.every(row => row.cells[column.id].kind === 'absent')).toBe(true);
	});
	it('keeps source HTML and formula-like content as literal strings for safe renderers', () => {
		const text = '=HYPERLINK("https://example.org", "<script>alert(1)</script>")';
		expect(one({ label: 'Synthetic hostile text', value: text, state: 'value' })).toMatchObject({ kind: 'value', display: text });
	});
});

// Compile-time sentinels: a sixth state or numeric payload on absence must stay rejected.
function checkClosedTypes(absent: Extract<FicheGridCell, { kind: 'absent' }>): void {
	// @ts-expect-error The grid never adds a sixth canonical state.
	const invalidState: FieldValue = { state: 'zero_denominator' };
	// @ts-expect-error An absence carries no formatted or numeric value.
	const numericAbsence: FicheGridCell = { ...absent, display: 0 };
	void invalidState;
	void numericAbsence;
}
void checkClosedTypes;
