import type { FieldPresentation, KpiItem, KvRow } from '$lib/types/fiche';
import type { FicheGrid, FicheGridCell, FicheGridColumn, FicheGridConcept, FicheGridOptions } from '$lib/types/fiche-grid';
import { datasetDuField, getExplication, hasExplication, millesimePour, sourcePourDataset } from './explications';
import { displayUnit } from './formatters';
import { EXPLICATIONS_ETATS, LIBELLES_ECARTS_HISTORIQUES } from './etats-libelles';
import { REFERENTIELS, SANS_REFERENTIEL } from './referentiels';

function conceptFor(item: KpiItem | KvRow, options: FicheGridOptions): FicheGridConcept {
	// Labels may supply a local identity, never a semantic registry key. Explicit ids take priority.
	const key = item.id ?? item.fieldId ?? `label:${item.label}`;
	return {
		id: `${options.id}:${'kind' in item && item.kind === 'context' ? 'context:' : ''}${key}`,
		label: item.label,
		fieldId: item.fieldId,
		definition: hasExplication(item.fieldId) ? getExplication(item.fieldId).definition : null,
		unit: 'unit' in item ? item.unit ?? null : item.fieldId ? displayUnit(item.fieldId) : null
	};
}

function cellFor(item: KpiItem | KvRow, column: FicheGridColumn, options: FicheGridOptions): FicheGridCell {
	const kpi = 'verdict' in item;
	const context = 'kind' in item && item.kind === 'context';
	const concept = conceptFor(item, options);
	const fieldId = item.fieldId;
	const covered = hasExplication(fieldId);
	const dataset = covered ? datasetDuField(fieldId) : null;
	const provenance = options.provenances ?? {};
	const millesimes = options.millesimes ?? {};
	const sources = options.sources ?? {};
	const reference = kpi && fieldId && covered ? REFERENTIELS[fieldId] ?? null : null;
	const ecart = kpi ? item.ecartTexte ?? null : null;
	const tolerance = ecart ? reference?.tolerancePct : null;
	const metadata = {
		grain: item.presentation?.grain ?? column.grain,
		unit: concept.unit,
		unitInline: !kpi,
		estDatable: covered,
		millesime: dataset ? millesimePour(dataset, column.scope, provenance, millesimes) : null,
		source: dataset ? sourcePourDataset(dataset, provenance, sources) : null,
		reserves: item.presentation?.reserves ?? [],
		...(fieldId && options.criticalSummaries?.[fieldId] ? { criticalSummary: options.criticalSummaries[fieldId] } : {}),
		details: {
			definition: concept.definition,
			ecart,
			tolerance: tolerance != null ? `Est considérée « proche » une valeur à ±${tolerance} % de la référence.` : null,
			reference,
			raisonAbsence: kpi && fieldId ? SANS_REFERENTIEL[fieldId] ?? null : null
		}
	};
	if (context) return { ...metadata, kind: 'context', text: String(item.value) };
	const presentation: FieldPresentation | undefined = item.presentation ??
		(!kpi && item.state ? { state: item.state } : undefined);
	if (!presentation) throw new Error(`Unqualified grid cell: ${concept.id}. Supply the source state, never parse its display.`);
	if (!kpi && item.state && item.state !== presentation.state) {
		throw new Error(`Conflicting grid states: ${concept.id}`);
	}
	if (presentation.state !== 'value') {
		return {
			...metadata, kind: 'absent', state: presentation.state,
			label: LIBELLES_ECARTS_HISTORIQUES[presentation.state], explanation: EXPLICATIONS_ETATS[presentation.state],
			reason: presentation.reason
		};
	}
	return {
		...metadata, kind: 'value', state: 'value', display: item.value,
		measuredZero: presentation.measuredZero,
		...(kpi ? { verdict: item.verdict, verdictLabel: item.verdictLabel, dpeClass: item.dpeClass, primary: item.primary } : {})
	};
}

/** The input must retain the actual table scope; no comparison or aggregation is performed. */
export function rowsToGrid(rows: KvRow[], column: FicheGridColumn, options: FicheGridOptions): FicheGrid {
	return toGrid(rows, column, options);
}

/** KPI states must be qualified by the mapper that knows the raw value and attribution decision. */
export function kpisToGrid(items: KpiItem[], column: FicheGridColumn, options: FicheGridOptions): FicheGrid {
	return toGrid(items, column, options);
}

function toGrid(items: (KpiItem | KvRow)[], column: FicheGridColumn, options: FicheGridOptions): FicheGrid {
	const seen = new Set<string>();
	const rows = items.map(item => {
		const concept = conceptFor(item, options);
		if (seen.has(concept.id)) throw new Error(`Duplicate grid concept: ${concept.id}. Supply explicit distinct ids.`);
		seen.add(concept.id);
		return { concept, cells: { [column.id]: cellFor(item, column, options) } };
	});
	return { id: options.id, columns: [column], rows };
}

/** Context reservations remain visible without inflating the number of indicators. */
export function gridIndicatorCount(grid: FicheGrid): number {
	return grid.rows.filter(row => Object.values(row.cells).some(cell => cell.kind !== 'context')).length;
}
