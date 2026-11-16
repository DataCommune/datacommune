import type { FieldValue, QualifiedAbsenceReason, Scope, Verdict } from './fiche';
import type { DatasetProvenance } from '$lib/api/resolve';
import type { SourceLive } from '$lib/data/explications';
import type { ReferentielNumerique, ReferentielCategoriel } from '$lib/data/referentiels';

/** Entity and table scope are explicit; a BAN code does not imply a building or dwelling. */
export interface FicheGridColumn {
	id: string;
	entity: 'commune' | 'bien' | 'adresse' | 'batiment';
	scope: Scope;
	grain: string;
	label: string;
	code?: string;
}

export interface FicheGridConcept {
	id: string;
	label: string;
	fieldId?: string;
	definition: string | null;
	unit: string | null;
}

export interface FicheGridDetails {
	definition: string | null;
	ecart: string | null;
	tolerance: string | null;
	reference: ReferentielNumerique | ReferentielCategoriel | null;
	raisonAbsence: string | null;
}

/** Concise qualified reading boundaries. Full reservations remain unchanged/exportable. */
export interface FicheCriticalSummary {
	facts: readonly string[];
	warnings: readonly string[];
}

interface CellMetadata {
	grain: string;
	unit: string | null;
	/** Row values already embed their unit; KPI values keep a separate unit slot. */
	unitInline: boolean;
	estDatable: boolean;
	millesime: string | null;
	source: { nom: string; url: string | null } | null;
	reserves: readonly string[];
	/** Missing summary keeps the full reservations visible; never truncate source text. */
	criticalSummary?: FicheCriticalSummary;
	details: FicheGridDetails;
}

export type FicheGridCell = CellMetadata & (
	| {
		kind: 'value'; state: 'value'; display: string | number; measuredZero?: boolean;
		verdict?: Verdict; verdictLabel?: string; dpeClass?: string; primary?: boolean
	}
	| {
		kind: 'absent'; state: Exclude<FieldValue['state'], 'value'>;
		label: string; explanation: string; reason?: QualifiedAbsenceReason
	}
	| { kind: 'context'; text: string }
);

export interface FicheGridRow {
	concept: FicheGridConcept;
	cells: Record<string, FicheGridCell>;
}

export interface FicheGrid {
	id: string;
	columns: FicheGridColumn[];
	rows: FicheGridRow[];
}

export interface FicheGridOptions {
	/** Table namespace, stable across value changes and rendering order changes. */
	id: string;
	millesimes?: Record<string, string | null>;
	provenances?: Record<string, DatasetProvenance>;
	sources?: Record<string, SourceLive>;
	criticalSummaries?: Record<string, FicheCriticalSummary>;
}
