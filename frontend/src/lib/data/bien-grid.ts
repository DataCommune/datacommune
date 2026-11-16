import type { CommuneSynthese } from '$lib/api/commune';
import type { DatasetProvenance, ResultatMultiResolution } from '$lib/api/resolve';
import type { FicheGrid, FicheGridColumn } from '$lib/types/fiche-grid';
import type { FieldPresentation, KvRow, ThemeModel } from '$lib/types/fiche';
import type { DpeFicheSelection } from './dpe-attribution-view';
import { mapBienToFiche, SELECTION_DPE_INDISPONIBLE } from './bien-fiche';
import { mapCommuneToGrid, criticalSummariesForCommune } from './commune-grid';
import { deriveDvfAttributionView } from './dvf-attribution-view';
import { kpisToGrid, rowsToGrid } from './fiche-grid';
import { millesimesFromCommune } from './explications';
import { enrichCommuneIndicatorPresentation } from './commune-indicator-presentation';

export interface BienGridOptions {
	provenances?: Record<string, DatasetProvenance>;
}

export interface BienGridModel {
	column: FicheGridColumn;
	communeColumn: FicheGridColumn;
	fiche: ReturnType<typeof mapBienToFiche>;
	synthese: FicheGrid;
	themes: Record<string, { bien: FicheGrid; commune: FicheGrid }>;
}

/** Qualified state comes from the existing mapper; neither display text nor payload rank decides it. */
function withGrain(row: KvRow, grain: string, reserves: readonly string[] = []): KvRow {
	const state: FieldPresentation | undefined = row.presentation ?? (row.state ? { state: row.state } : undefined);
	return { ...row, presentation: state ? { ...state, grain, reserves: [...(state.reserves ?? []), ...reserves] } : undefined };
}

/** A public address dossier is not proof of dwelling identity. No inventory/document is read. */
export function mapBienToGrid(
	multi: ResultatMultiResolution,
	commune: CommuneSynthese | null,
	dpe: DpeFicheSelection = SELECTION_DPE_INDISPONIBLE,
	options: BienGridOptions = {}
): BienGridModel {
	const mapped = mapBienToFiche(multi, commune, dpe);
	const communal = commune ? mapCommuneToGrid(commune) : null;
	const column: FicheGridColumn = {
		id: 'dossier', entity: 'adresse', scope: 'bien',
		grain: multi.adresse ? 'Adresse' : 'Point consulté', label: multi.adresse ?? 'Adresse non disponible'
	};
	const communeCode = commune?.code_insee ?? multi.code_insee ?? undefined;
	const communeColumn: FicheGridColumn = communal?.column ?? {
		id: communeCode ? `commune:${communeCode}` : 'commune', entity: 'commune', scope: 'commune',
		grain: 'Commune', label: multi.commune ?? communeCode ?? 'Commune non renseignée', code: communeCode
	};
	// Existing DpeAttributionStatus copy, repeated next to the qualified gap and in CSV.
	const dpeReserves = dpe.diagnostic != null ? [] : dpe.gapState === 'temporary_error'
		? ['Attribution DPE momentanément indisponible']
		: ['Attribution au logement non établie', 'Des diagnostics peuvent être connus à cette adresse, mais aucun document n’est attribué au logement recherché.'];
	const kpi = mapped.kpi.map(item => item.fieldId === 'bien.dpe.etiquette_dpe'
		? { ...item, presentation: item.presentation ? { ...item.presentation, reserves: dpeReserves } : undefined }
		: item);
	const dvfReserves = kpi.find(item => item.fieldId === 'bien.dvf.prix_m2')?.presentation?.reserves ?? [];
	const themes: ThemeModel[] = mapped.themes.map(theme => {
		const rowsBien = (theme.rowsBien ?? []).map(row => {
			if (row.fieldId?.startsWith('bien.dvf.')) return withGrain(row, 'Parcelle');
			if (row.fieldId?.startsWith('bien.cadastre.')) return withGrain(row, 'Parcelle');
			if (row.fieldId?.startsWith('bien.dpe.')) return withGrain(row, 'Logement', dpeReserves);
			if (row.fieldId?.startsWith('bien.risques.') || row.fieldId?.startsWith('bien.urbanisme.')) return withGrain(row, 'Point consulté');
			return row;
		});
		if (theme.key === 'marche' && dvfReserves.length) rowsBien.push({ kind: 'context', label: 'Attribution des ventes', value: dvfReserves.join(' ') });
		// Reuse Commune qualification while retaining independent proxy rows and exclusions.
		const qualifiedCommune = communal?.fiche.themes.find(candidate => candidate.key === theme.key)?.rows ?? [];
		const keyFor = (row: KvRow) => row.fieldId ?? `${row.kind ?? 'row'}:${row.label}`;
		const byKey = new Map(qualifiedCommune.map(row => [keyFor(row), row]));
		const originalKeys = new Set((theme.rowsCommune ?? []).map(keyFor));
		const rowsCommune = (theme.rowsCommune ?? []).map(row => byKey.get(keyFor(row)) ?? row);
		rowsCommune.push(...qualifiedCommune.filter(row => row.kind === 'context' && !originalKeys.has(keyFor(row))));
		return { ...theme, rowsBien, rowsCommune };
	});
	const dvf = deriveDvfAttributionView(multi.dvf_attribution);
	const criticalSummaries = {
		...(commune ? criticalSummariesForCommune(commune) : {}),
		...(dpe.diagnostic == null ? { 'bien.dpe.etiquette_dpe': {
			facts: [], warnings: [dpe.gapState === 'temporary_error' ? 'Attribution DPE momentanément indisponible' : 'Attribution au logement non établie']
		} } : {}),
		...(dvf.mention ? { 'bien.dvf.prix_m2': { facts: [], warnings: [dvf.mention] } } : {})
	};
	const metadata = { criticalSummaries, provenances: options.provenances ?? {}, millesimes: commune ? millesimesFromCommune(commune) : {} };
	return {
		column, communeColumn, fiche: { kpi, themes },
		synthese: kpisToGrid(kpi, column, { ...metadata, id: 'bien-synthese' }),
		themes: Object.fromEntries(themes.map(theme => [theme.key, {
			bien: rowsToGrid(theme.rowsBien ?? [], column, { ...metadata, id: `bien-${theme.key}` }),
			commune: enrichCommuneIndicatorPresentation(
				rowsToGrid(theme.rowsCommune ?? [], communeColumn, { ...metadata, id: `bien-commune-${theme.key}` }),
				commune ?? undefined
			)
		}]))
	};
}
