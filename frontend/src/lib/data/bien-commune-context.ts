import type { CommuneSynthese } from '$lib/api/commune';
import type { FicheGrid, FicheGridColumn } from '$lib/types/fiche-grid';
import { rowsToGrid } from './fiche-grid';
import { mapCommuneIndicators } from './commune-indicators';
import { enrichCommuneIndicatorPresentation } from './commune-indicator-presentation';

const COMMUNE_COLUMN = {
	id: 'commune',
	entity: 'commune',
	scope: 'commune',
	grain: 'Commune',
	label: 'Commune'
} satisfies Omit<FicheGridColumn, 'code'>;

/**
 * Places the qualified commune projection in a Bien fiche without changing its
 * values, formats, states, periods, populations or reservations.
 */
export function mapBienCommuneContext(commune: CommuneSynthese | null): FicheGrid | null {
	if (!commune) return null;

	const indicators = mapCommuneIndicators(commune);
	const rows = [...indicators.marche, ...indicators.energie];
	if (rows.length === 0) return null;

	return enrichCommuneIndicatorPresentation(rowsToGrid(
		rows,
		{ ...COMMUNE_COLUMN, code: commune.code_insee },
		{ id: 'bien-commune-context' }
	), commune);
}
