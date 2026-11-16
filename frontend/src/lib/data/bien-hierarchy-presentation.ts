import type { FicheCriticalSummary, FicheGrid, FicheGridCell, FicheGridRow } from '$lib/types/fiche-grid';
import type { BienHierarchyModel } from './bien-hierarchy';

export interface BienHierarchyPresentation {
	primary: FicheGrid;
	secondary: FicheGrid;
	print: FicheGrid;
}

/** Avoid duplicate reading only when identity, label and every cell qualifier agree. */
export function withoutRepeatedHierarchyContext(grid: FicheGrid, hierarchy: BienHierarchyModel | null): FicheGrid {
	if (!hierarchy) return grid;
	const signature = (row: FicheGridRow, cell: FicheGridCell) => JSON.stringify([
		row.concept.id.slice(row.concept.id.lastIndexOf(':') + 1), row.concept.label, row.concept.definition, row.concept.unit, cell
	]);
	const alreadyShown = new Set(hierarchy.grid.rows.flatMap((row) =>
		row.cells.commune ? [signature(row, row.cells.commune)] : []
	));
	return { ...grid, rows: grid.rows.filter((row) =>
		!Object.values(row.cells).every((cell) => alreadyShown.has(signature(row, cell)))
	) };
}

const PRIMARY_IDS = new Set([
	'dpe-attribution',
	'dpe-document-count',
	'dpe-distribution',
	'dpe-rnb-context'
]);
const PRIMARY_SUFFIXES = [
	'dvf-maison-mediane-courante',
	'dvf-maison-variation',
	'dvf-appartement-mediane-courante',
	'dvf-appartement-variation',
	'dpe-mediane',
	'dpe-modes',
	'dpe-part-fg',
	'dvf-indicateurs-absence',
	'dpe-indicateurs-absence',
	'dpe-resultat-absence'
] as const;
const DISCLOSURE_ONLY = /^(?:Source|Source\(s\)|Producteur|Licence|Référence|Publication source|Snapshot|Import|Génération|Calcul)\s*:/;
const DOCUMENTARY_POPULATION = /^Population\s*:\s*\S+\.v\d+$/i;

function unique(values: readonly string[]): string[] {
	return [...new Set(values)];
}

function summaryFor(cell: FicheGridCell): FicheCriticalSummary | undefined {
	const existing = cell.criticalSummary;
	if (existing != null) return { facts: [...existing.facts], warnings: [...existing.warnings] };
	const inline = cell.reserves
		.flatMap((reserve) => reserve.split(' · ').map((segment) => segment.trim()).filter(Boolean))
		.filter((reserve) => !DISCLOSURE_ONLY.test(reserve) && !DOCUMENTARY_POPULATION.test(reserve));
	if (cell.reserves.length === 0) return undefined;
	return {
		facts: [],
		warnings: unique(inline)
	};
}

function presentCell(cell: FicheGridCell): FicheGridCell {
	const criticalSummary = summaryFor(cell);
	const copy = { ...cell, reserves: [...cell.reserves] };
	if (criticalSummary == null) {
		delete copy.criticalSummary;
		return copy;
	}
	return { ...copy, criticalSummary };
}

function presentRow(row: FicheGridRow, communeOnly: boolean): FicheGridRow {
	const entries = communeOnly
		? Object.entries(row.cells).filter(([columnId]) => columnId === 'commune')
		: Object.entries(row.cells);
	return {
		concept: { ...row.concept },
		cells: Object.fromEntries(entries.map(([columnId, cell]) => [columnId, presentCell(cell)]))
	};
}

function isPrimary(id: string): boolean {
	return PRIMARY_IDS.has(id) || PRIMARY_SUFFIXES.some((suffix) => id.endsWith(`:${suffix}`));
}

function hasNonCommunalContent(row: FicheGridRow): boolean {
	if (row.cells.commune == null) return true;
	return Object.entries(row.cells).some(([columnId, cell]) =>
		columnId !== 'commune' && (cell.kind !== 'absent' || cell.state !== 'not_applicable')
	);
}

function isStructuralPlaceholder(cell: FicheGridCell): boolean {
	const hasCriticalQualification = (cell.criticalSummary?.facts.length ?? 0) > 0 ||
		(cell.criticalSummary?.warnings.length ?? 0) > 0;
	return cell.kind === 'absent' && cell.state === 'not_applicable' && cell.reserves.length === 0 &&
		cell.reason == null && cell.details.definition === null && !hasCriticalQualification;
}

function printCell(cell: FicheGridCell): FicheGridCell {
	const copy = { ...cell, reserves: unique([
		...cell.reserves,
		...(cell.criticalSummary?.facts ?? []),
		...(cell.criticalSummary?.warnings ?? [])
	]) };
	delete copy.criticalSummary;
	return copy;
}

function printGrid(model: BienHierarchyModel): FicheGrid {
	const rows = model.grid.rows.flatMap((row) => Object.entries(row.cells).flatMap(([columnId, cell]) => {
		if (isStructuralPlaceholder(cell)) return [];
		return [{
			concept: {
				...row.concept,
				id: `${row.concept.id}:${columnId}`,
				label: `${row.concept.label} — ${cell.grain}`
			},
			cells: { scope: printCell(cell) }
		} satisfies FicheGridRow];
	}));
	return {
		id: `${model.grid.id}-print`,
		columns: [{ id: 'scope', entity: 'bien', scope: 'bien', grain: 'Mailles explicites', label: 'Valeur, portée et réserves' }],
		rows
	};
}

/** Presentation-only partition. The complete source model remains the CSV/print contract. */
export function presentBienHierarchy(model: BienHierarchyModel): BienHierarchyPresentation {
	const primaryRows: FicheGridRow[] = [];
	const secondaryRows: FicheGridRow[] = [];
	for (const row of model.grid.rows) {
		if (isPrimary(row.concept.id) || hasNonCommunalContent(row)) primaryRows.push(presentRow(row, false));
		else secondaryRows.push(presentRow(row, true));
	}
	const communeColumn = model.grid.columns.find((column) => column.id === 'commune');
	return {
		primary: { id: `${model.grid.id}-primary`, columns: model.grid.columns.map((column) => ({ ...column })), rows: primaryRows },
		secondary: { id: `${model.grid.id}-secondary`, columns: communeColumn ? [{ ...communeColumn }] : [], rows: secondaryRows },
		print: printGrid(model)
	};
}
