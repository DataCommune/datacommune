import { ApiError, api } from './client';

export const DPE_INVENTORY_ROUTE = '/v1/bien/dpe/inventaire';
export const DPE_DOCUMENT_ROUTE = '/v1/bien/dpe/document';
export const DPE_INVENTORY_DEFAULT_PAGE_SIZE = 20;
export const DPE_INVENTORY_MAX_PAGE_SIZE = 50;

export const DPE_INVENTORY_ERROR_STATUS = {
	invalid_reference: 400,
	refresh_required: 409,
	temporary_unavailable: 503
} as const;

export const DPE_DOCUMENT_UNITS = {
	surface_habitable: 'm² habitables DPE',
	conso_5_usages_ep: 'kWhEP/an',
	emission_ges_5_usages: 'kgCO₂eq/an',
	cout_total_5_usages: '€/an'
} as const;

export type DpeInventoryErrorCode = keyof typeof DPE_INVENTORY_ERROR_STATUS;
export type DpeInventoryErrorStatus = (typeof DPE_INVENTORY_ERROR_STATUS)[DpeInventoryErrorCode];

export type DpeInventoryApiError = {
	readonly [Code in DpeInventoryErrorCode]: {
		readonly code: Code;
		readonly status: (typeof DPE_INVENTORY_ERROR_STATUS)[Code];
	};
}[DpeInventoryErrorCode];

export type DpeInventoryState = 'ready' | 'absent' | 'identity_unavailable' | 'unavailable';
export type DpeInventoryReadState = 'complete' | 'partial';
export type DpeInventoryGraphState = 'not_checked' | 'incoherent';
export type DpeValidityByDate = 'unknown' | 'expired' | 'not_expired_by_date';

export interface DpeLifecycle {
	readonly source_state: 'present' | 'absent_from_complete_scope' | 'explicitly_deactivated' | 'unknown';
	readonly source_snapshot_date: string | null;
	readonly source_scope: '44/85';
	readonly regimes: readonly ('existant' | 'neuf')[];
	readonly replacement_state: 'not_checked' | 'no_successor_known' | 'replaced' | 'ambiguous';
	readonly graph_issues: readonly ('cycle' | 'fork' | 'missing_target' | 'truncated' | 'ambiguous_target')[];
	readonly validity: DpeValidityByDate | 'inconsistent_dates' | 'future_establishment';
	readonly as_of_date: string;
	readonly revision_status: 'unverified' | 'verified_attestation';
	readonly revision_kind: '2024' | '2026' | null;
	readonly revision_date: string | null;
	readonly detail_availability: 'available' | 'source_reconciled';
}

export interface DpeInventoryDistribution {
	readonly A: number;
	readonly B: number;
	readonly C: number;
	readonly D: number;
	readonly E: number;
	readonly F: number;
	readonly G: number;
	readonly unknown: number;
}

export interface DpeInventoryItem {
	/** Optional only for compatibility with previously published V2 envelopes. */
	readonly lifecycle?: DpeLifecycle | null;
	readonly document_ref: string;
	readonly type_batiment: string | null;
	readonly surface_habitable: number | null;
	readonly etiquette_dpe: string | null;
	readonly etiquette_ges: string | null;
	readonly date_etablissement_dpe: string | null;
	readonly date_fin_validite_dpe: string | null;
}

export type DpeEnergyClass = 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G';

export interface DpePublicProvenance {
	readonly association: 'source_ban_equality';
	readonly address_check: 'unverified';
	readonly source: 'ADEME';
	readonly source_coverage: 'unknown';
	readonly graph_state: DpeInventoryGraphState;
	readonly imported_at: string | null;
}

export interface DpeInventoryPage extends DpePublicProvenance {
	readonly as_of_date?: string | null;
	readonly version: 2;
	readonly state: DpeInventoryState;
	readonly context_ref: string | null;
	readonly items: readonly DpeInventoryItem[];
	readonly total: number | null;
	readonly distribution: DpeInventoryDistribution;
	readonly page_size: number;
	readonly has_more: boolean;
	readonly next_cursor: string | null;
	readonly read_state: DpeInventoryReadState;
}

export interface DpeConsultedDocument extends DpeInventoryItem, DpePublicProvenance {
	readonly version: 2;
	readonly context_ref: string;
	readonly annee_construction: number | null;
	readonly periode_construction: string | null;
	readonly type_installation_chauffage: string | null;
	readonly type_energie_principale_chauffage: string | null;
	readonly qualite_isolation_enveloppe: string | null;
	readonly conso_5_usages_ep: number | null;
	readonly emission_ges_5_usages: number | null;
	readonly cout_total_5_usages: number | null;
	readonly validity_by_date: DpeValidityByDate;
}

export interface DpeBuildingContextV2 {
	readonly count: number;
	/** Runtime projection is A–G only; `unknown` remains optional for Plan03 type compatibility. */
	readonly distribution: Readonly<Partial<DpeInventoryDistribution>>;
	readonly truncated: boolean;
}

interface DpeAttributionV2Shared {
	readonly version: 2;
	readonly diagnostic: null;
	readonly candidates: readonly [];
	readonly graph_state: DpeInventoryGraphState;
}

export type DpeAttributionV2 =
	| (DpeAttributionV2Shared & {
			readonly status: 'unassigned';
			readonly reason: 'dwelling_identity_unproven';
			readonly building_context: DpeBuildingContextV2 | null;
	  })
	| (DpeAttributionV2Shared & {
			readonly status: 'absent';
			readonly reason: 'identity_unavailable' | 'not_reported';
			readonly building_context: DpeBuildingContextV2 | null;
	  })
	| (DpeAttributionV2Shared & {
			readonly status: 'error';
			readonly reason: 'temporary_error';
			readonly building_context: null;
	  });

/**
 * Local fail-closed state used while an old or malformed attribution payload is being
 * refreshed. It deliberately carries no diagnostic, candidate or building aggregate.
 */
export interface DpeAttributionContractUnavailable {
	readonly version: 2;
	readonly status: 'unavailable';
	readonly reason: 'contract_unavailable';
	readonly diagnostic: null;
	readonly candidates: readonly [];
	readonly building_context: null;
	readonly graph_state: 'not_checked';
}

export type DpeAttributionClient = DpeAttributionV2 | DpeAttributionContractUnavailable;

export type DpeInventoryContractErrorCode = 'invalid_payload' | 'context_mismatch';

const CONTRACT_ERROR_MESSAGES: Record<DpeInventoryContractErrorCode, string> = {
	invalid_payload: 'Invalid DPE public payload.',
	context_mismatch: 'DPE document context does not match.'
};

export class DpeInventoryContractError extends Error {
	readonly code: DpeInventoryContractErrorCode;

	constructor(code: DpeInventoryContractErrorCode) {
		super(CONTRACT_ERROR_MESSAGES[code]);
		this.name = 'DpeInventoryContractError';
		this.code = code;
	}
}

const REQUEST_ERROR_MESSAGES: Record<DpeInventoryErrorCode, string> = {
	invalid_reference: 'La référence DPE est invalide.',
	refresh_required: 'Les données DPE doivent être actualisées.',
	temporary_unavailable: 'Les données DPE sont momentanément indisponibles.'
};

/** Sanitized request error: opaque references and upstream bodies never reach the message. */
export class DpeInventoryRequestError extends Error {
	readonly code: DpeInventoryErrorCode;
	readonly status: DpeInventoryErrorStatus;

	constructor(code: DpeInventoryErrorCode) {
		super(REQUEST_ERROR_MESSAGES[code]);
		this.name = 'DpeInventoryRequestError';
		this.code = code;
		this.status = DPE_INVENTORY_ERROR_STATUS[code];
	}
}

type JsonRecord = Record<string, unknown>;

const INVENTORY_STATES: readonly DpeInventoryState[] = [
	'ready',
	'absent',
	'identity_unavailable',
	'unavailable'
];
const READ_STATES: readonly DpeInventoryReadState[] = ['complete', 'partial'];
const GRAPH_STATES: readonly DpeInventoryGraphState[] = ['not_checked', 'incoherent'];
const VALIDITY_STATES: readonly DpeValidityByDate[] = ['unknown', 'expired', 'not_expired_by_date'];

function invalidPayload(): never {
	throw new DpeInventoryContractError('invalid_payload');
}

function asRecord(value: unknown): JsonRecord {
	if (value === null || typeof value !== 'object' || Array.isArray(value)) {
		return invalidPayload();
	}
	return value as JsonRecord;
}

function asArray(value: unknown): unknown[] {
	if (!Array.isArray(value)) return invalidPayload();
	return value;
}

function asString(value: unknown): string {
	if (typeof value !== 'string') return invalidPayload();
	return value;
}

function asNonEmptyString(value: unknown): string {
	const result = asString(value);
	if (result.trim().length === 0) return invalidPayload();
	return result;
}

function asNullableString(value: unknown): string | null {
	if (value === null) return null;
	return asString(value);
}

function asNullableNonEmptyString(value: unknown): string | null {
	if (value === null) return null;
	return asNonEmptyString(value);
}

function asNullableIsoDate(value: unknown): string | null {
	if (value === null) return null;
	const result = asString(value);
	if (!/^\d{4}-\d{2}-\d{2}$/.test(result)) return invalidPayload();
	const parsed = new Date(`${result}T00:00:00.000Z`);
	if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== result) {
		return invalidPayload();
	}
	return result;
}

function asNumber(value: unknown): number {
	if (typeof value !== 'number' || !Number.isFinite(value)) return invalidPayload();
	return value;
}

function asNullableNumber(value: unknown): number | null {
	if (value === null) return null;
	return asNumber(value);
}

function asBoolean(value: unknown): boolean {
	if (typeof value !== 'boolean') return invalidPayload();
	return value;
}

function asNonNegativeInteger(value: unknown): number {
	const result = asNumber(value);
	if (!Number.isInteger(result) || result < 0) return invalidPayload();
	return result;
}

function asNullableNonNegativeInteger(value: unknown): number | null {
	if (value === null) return null;
	return asNonNegativeInteger(value);
}

function asLiteral<T extends string>(value: unknown, allowed: readonly T[]): T {
	if (typeof value !== 'string' || !allowed.includes(value as T)) return invalidPayload();
	return value as T;
}

const LIFECYCLE_FIELDS = [
	'source_state', 'source_snapshot_date', 'source_scope', 'regimes', 'replacement_state',
	'graph_issues', 'validity', 'as_of_date', 'revision_status', 'revision_kind', 'revision_date',
	'detail_availability'
] as const;

function literalList<T extends string>(value: unknown, allowed: readonly T[]): T[] {
	const result = asArray(value).map((entry) => asLiteral(entry, allowed));
	if (new Set(result).size !== result.length) invalidPayload();
	return result;
}

/** A recognized reconciliation signal can only remove content, never grant a public state. */
function isReconciled(value: unknown): boolean {
	if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
	const input = value as JsonRecord;
	return input.source_state === 'absent_from_complete_scope' ||
		input.source_state === 'explicitly_deactivated' || input.detail_availability === 'source_reconciled';
}

/** Validate against the provided calendar date, never the browser clock. */
function lifecycleMatchesDates(lifecycle: DpeLifecycle, established: string | null, end: string | null): boolean {
	if (lifecycle.detail_availability === 'source_reconciled') return lifecycle.validity === 'unknown';
	if (lifecycle.validity === 'inconsistent_dates' && (established === null || end === null)) return true;
	const expected = established !== null && end !== null && end < established ? 'inconsistent_dates'
		: established !== null && established > lifecycle.as_of_date ? 'future_establishment'
		: established === null || end === null ? 'unknown'
		: end < lifecycle.as_of_date ? 'expired' : 'not_expired_by_date';
	return lifecycle.validity === expected;
}

/** Entire public contract or null: a partial lifecycle is never serialized as an API object. */
function projectLifecycle(value: unknown, established: string | null, end: string | null, pageDate?: string | null): DpeLifecycle | null {
	if (value == null) return null;
	try {
		const input = asRecord(value);
		if (Object.keys(input).length !== LIFECYCLE_FIELDS.length ||
			LIFECYCLE_FIELDS.some((key) => !Object.hasOwn(input, key))) invalidPayload();
		const asOfDate = asNullableIsoDate(input.as_of_date);
		if (asOfDate === null || (pageDate !== undefined && asOfDate !== pageDate)) invalidPayload();
		const lifecycle: DpeLifecycle = {
			source_state: asLiteral(input.source_state, ['present', 'absent_from_complete_scope', 'explicitly_deactivated', 'unknown']),
			source_snapshot_date: asNullableIsoDate(input.source_snapshot_date),
			source_scope: asLiteral(input.source_scope, ['44/85']),
			regimes: literalList(input.regimes, ['existant', 'neuf']),
			replacement_state: asLiteral(input.replacement_state, ['not_checked', 'no_successor_known', 'replaced', 'ambiguous']),
			graph_issues: literalList(input.graph_issues, ['cycle', 'fork', 'missing_target', 'truncated', 'ambiguous_target']),
			validity: asLiteral(input.validity, ['unknown', 'expired', 'not_expired_by_date', 'inconsistent_dates', 'future_establishment']),
			as_of_date: asOfDate,
			revision_status: asLiteral(input.revision_status, ['unverified', 'verified_attestation']),
			revision_kind: input.revision_kind === null ? null : asLiteral(input.revision_kind, ['2024', '2026'] as const),
			revision_date: asNullableIsoDate(input.revision_date),
			detail_availability: asLiteral(input.detail_availability, ['available', 'source_reconciled'])
		};
		if (lifecycle.source_state !== 'unknown' && lifecycle.source_snapshot_date === null) invalidPayload();
		if (lifecycle.source_snapshot_date !== null && lifecycle.source_snapshot_date > asOfDate) invalidPayload();
		if (lifecycle.revision_status === 'unverified'
			? lifecycle.revision_kind !== null || lifecycle.revision_date !== null
			: lifecycle.revision_kind === null || lifecycle.revision_date === null) invalidPayload();
		if (lifecycle.revision_date !== null && lifecycle.revision_date > asOfDate) invalidPayload();
		const absent = lifecycle.source_state === 'absent_from_complete_scope' || lifecycle.source_state === 'explicitly_deactivated';
		if (absent !== (lifecycle.detail_availability === 'source_reconciled')) invalidPayload();
		if (!lifecycleMatchesDates(lifecycle, established, end)) invalidPayload();
		return lifecycle;
	} catch (error) {
		if (error instanceof DpeInventoryContractError) return null;
		throw error;
	}
}

function projectInventoryItem(value: unknown, pageDate?: string | null): DpeInventoryItem {
	const input = asRecord(value);
	const reconciled = isReconciled(input.lifecycle);
	const established = reconciled ? null : asNullableIsoDate(input.date_etablissement_dpe);
	const end = reconciled ? null : asNullableIsoDate(input.date_fin_validite_dpe);
	return {
		document_ref: asNonEmptyString(input.document_ref),
		type_batiment: reconciled ? null : asNullableString(input.type_batiment),
		surface_habitable: reconciled ? null : asNullableNumber(input.surface_habitable),
		etiquette_dpe: reconciled ? null : asNullableString(input.etiquette_dpe),
		etiquette_ges: reconciled ? null : asNullableString(input.etiquette_ges),
		date_etablissement_dpe: established,
		date_fin_validite_dpe: end,
		lifecycle: projectLifecycle(input.lifecycle, established, end, pageDate)
	};
}

function projectDistribution(value: unknown): DpeInventoryDistribution {
	const input = asRecord(value);
	return {
		A: asNonNegativeInteger(input.A),
		B: asNonNegativeInteger(input.B),
		C: asNonNegativeInteger(input.C),
		D: asNonNegativeInteger(input.D),
		E: asNonNegativeInteger(input.E),
		F: asNonNegativeInteger(input.F),
		G: asNonNegativeInteger(input.G),
		unknown: asNonNegativeInteger(input.unknown)
	};
}

const DPE_ENERGY_CLASSES: readonly DpeEnergyClass[] = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

function projectBuildingDistribution(
	value: unknown
): Readonly<Partial<Record<DpeEnergyClass, number>>> {
	const input = asRecord(value);
	if (Object.keys(input).some((key) => !DPE_ENERGY_CLASSES.includes(key as DpeEnergyClass))) {
		return invalidPayload();
	}
	const result: Partial<Record<DpeEnergyClass, number>> = {};
	for (const key of DPE_ENERGY_CLASSES) {
		if (input[key] !== undefined) result[key] = asNonNegativeInteger(input[key]);
	}
	return result;
}

function projectBuildingContext(value: unknown): DpeBuildingContextV2 | null {
	if (value === null) return null;
	const input = asRecord(value);
	const context = {
		count: asNonNegativeInteger(input.count),
		distribution: projectBuildingDistribution(input.distribution),
		truncated: asBoolean(input.truncated)
	};
	const distributed = Object.values(context.distribution).reduce((sum, count) => sum + count, 0);
	if (distributed > context.count) invalidPayload();
	return context;
}

function projectProvenance(value: JsonRecord): DpePublicProvenance {
	return {
		association: asLiteral(value.association, ['source_ban_equality']),
		address_check: asLiteral(value.address_check, ['unverified']),
		source: asLiteral(value.source, ['ADEME']),
		source_coverage: asLiteral(value.source_coverage, ['unknown']),
		graph_state: asLiteral(value.graph_state, GRAPH_STATES),
		imported_at: asNullableString(value.imported_at)
	};
}

function distributionTotal(distribution: DpeInventoryDistribution): number {
	return (
		distribution.A +
		distribution.B +
		distribution.C +
		distribution.D +
		distribution.E +
		distribution.F +
		distribution.G +
		distribution.unknown
	);
}

function validatePage(page: DpeInventoryPage): void {
	if (page.page_size < 1 || page.page_size > DPE_INVENTORY_MAX_PAGE_SIZE) invalidPayload();
	if (page.items.length > page.page_size) invalidPayload();
	if (page.has_more !== (page.next_cursor !== null)) invalidPayload();
	if (page.items.length > 0 && page.state !== 'ready') invalidPayload();
	if (page.state !== 'ready' && page.has_more) invalidPayload();
	if (page.state === 'ready' && page.context_ref === null) invalidPayload();
	if (page.state === 'absent' && page.total !== 0) invalidPayload();
	if (
		(page.state === 'identity_unavailable' || page.state === 'unavailable') &&
		(page.total !== null || distributionTotal(page.distribution) !== 0)
	)
		invalidPayload();
	if (page.state === 'identity_unavailable' && page.context_ref !== null) invalidPayload();
	if (page.total !== null && page.items.length > page.total) invalidPayload();
	if (page.total !== null && distributionTotal(page.distribution) !== page.total) invalidPayload();
}

/** Project the versioned attribution without accepting any Phase 51 fallback shape. */
export function projectDpeAttributionV2(value: unknown): DpeAttributionV2 {
	const input = asRecord(value);
	if (asNumber(input.version) !== 2) return invalidPayload();
	if (input.diagnostic !== null) return invalidPayload();
	if (asArray(input.candidates).length !== 0) return invalidPayload();

	const graphState = asLiteral(input.graph_state, GRAPH_STATES);
	const status = asLiteral(input.status, ['unassigned', 'absent', 'error'] as const);
	if (status === 'unassigned') {
		return {
			version: 2,
			status,
			reason: asLiteral(input.reason, ['dwelling_identity_unproven'] as const),
			diagnostic: null,
			candidates: [],
			building_context: projectBuildingContext(input.building_context),
			graph_state: graphState
		};
	}
	if (status === 'absent') {
		return {
			version: 2,
			status,
			reason: asLiteral(input.reason, ['identity_unavailable', 'not_reported'] as const),
			diagnostic: null,
			candidates: [],
			building_context: projectBuildingContext(input.building_context),
			graph_state: graphState
		};
	}
	if (input.building_context !== null) return invalidPayload();
	return {
		version: 2,
		status,
		reason: asLiteral(input.reason, ['temporary_error'] as const),
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: graphState
	};
}

export function unavailableDpeAttribution(): DpeAttributionContractUnavailable {
	return {
		version: 2,
		status: 'unavailable',
		reason: 'contract_unavailable',
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: 'not_checked'
	};
}

/**
 * A legacy exact/multiple payload is not an attribution proof under V2. Keep the surrounding
 * response usable and expose a neutral refresh state instead of reviving the old decision.
 */
export function projectDpeAttributionForClient(value: unknown): DpeAttributionClient {
	try {
		return projectDpeAttributionV2(value);
	} catch (error) {
		if (error instanceof DpeInventoryContractError) return unavailableDpeAttribution();
		throw error;
	}
}

export function projectDpeInventoryPage(
	value: unknown,
	expectedContextRef?: string
): DpeInventoryPage {
	const input = asRecord(value);
	const version = asNumber(input.version);
	if (version !== 2) return invalidPayload();
	const provenance = projectProvenance(input);
	const asOfDate = asNullableIsoDate(input.as_of_date ?? null);
	const page: DpeInventoryPage = {
		version: 2,
		as_of_date: asOfDate,
		state: asLiteral(input.state, INVENTORY_STATES),
		context_ref: asNullableNonEmptyString(input.context_ref),
		items: asArray(input.items).map((item) => projectInventoryItem(item, asOfDate)),
		total: asNullableNonNegativeInteger(input.total),
		distribution: projectDistribution(input.distribution),
		page_size: asNonNegativeInteger(input.page_size),
		has_more: asBoolean(input.has_more),
		next_cursor: asNullableNonEmptyString(input.next_cursor),
		read_state: asLiteral(input.read_state, READ_STATES),
		association: provenance.association,
		address_check: provenance.address_check,
		source: provenance.source,
		source_coverage: provenance.source_coverage,
		graph_state: provenance.graph_state,
		imported_at: provenance.imported_at
	};
	validatePage(page);
	if (expectedContextRef !== undefined && page.context_ref !== expectedContextRef) {
		throw new DpeInventoryContractError('context_mismatch');
	}
	return page;
}

/**
 * An embedded inventory must never make the surrounding DVF/cadastre response unusable.
 * Dedicated inventory requests stay strict; only this optional envelope field degrades to null.
 */
export function projectEmbeddedDpeInventory(value: unknown): DpeInventoryPage | null {
	try {
		return projectDpeInventoryPage(value);
	} catch (error) {
		if (error instanceof DpeInventoryContractError) return null;
		throw error;
	}
}

function validateDocument(
	document: DpeConsultedDocument,
	expectedContextRef: string,
	expectedDocumentRef?: string
): void {
	if (document.context_ref !== expectedContextRef) {
		throw new DpeInventoryContractError('context_mismatch');
	}
	if (expectedDocumentRef !== undefined && document.document_ref !== expectedDocumentRef) {
		invalidPayload();
	}
	const endDate = document.date_fin_validite_dpe;
	if (endDate === null && document.validity_by_date !== 'unknown') invalidPayload();
	if (document.lifecycle != null) {
		const expected = ['inconsistent_dates', 'future_establishment'].includes(document.lifecycle.validity)
			? 'unknown' : document.lifecycle.validity;
		if (document.validity_by_date !== expected) invalidPayload();
	}
}

export function projectDpeConsultedDocument(
	value: unknown,
	expectedContextRef: string,
	expectedDocumentRef?: string
): DpeConsultedDocument {
	const input = asRecord(value);
	const version = asNumber(input.version);
	if (version !== 2) return invalidPayload();
	const item = projectInventoryItem(input);
	const reconciled = isReconciled(input.lifecycle);
	const provenance = projectProvenance(input);
	const document: DpeConsultedDocument = {
		version: 2,
		context_ref: asNonEmptyString(input.context_ref),
		document_ref: item.document_ref,
		type_batiment: item.type_batiment,
		surface_habitable: item.surface_habitable,
		etiquette_dpe: item.etiquette_dpe,
		etiquette_ges: item.etiquette_ges,
		date_etablissement_dpe: item.date_etablissement_dpe,
		date_fin_validite_dpe: item.date_fin_validite_dpe,
		lifecycle: item.lifecycle,
		annee_construction: reconciled ? null : asNullableNonNegativeInteger(input.annee_construction),
		periode_construction: reconciled ? null : asNullableString(input.periode_construction),
		type_installation_chauffage: reconciled ? null : asNullableString(input.type_installation_chauffage),
		type_energie_principale_chauffage: reconciled ? null : asNullableString(input.type_energie_principale_chauffage),
		qualite_isolation_enveloppe: reconciled ? null : asNullableString(input.qualite_isolation_enveloppe),
		conso_5_usages_ep: reconciled ? null : asNullableNumber(input.conso_5_usages_ep),
		emission_ges_5_usages: reconciled ? null : asNullableNumber(input.emission_ges_5_usages),
		cout_total_5_usages: reconciled ? null : asNullableNumber(input.cout_total_5_usages),
		validity_by_date: reconciled ? 'unknown' : asLiteral(input.validity_by_date, VALIDITY_STATES),
		association: provenance.association,
		address_check: provenance.address_check,
		source: provenance.source,
		source_coverage: provenance.source_coverage,
		graph_state: provenance.graph_state,
		imported_at: provenance.imported_at
	};
	validateDocument(document, expectedContextRef, expectedDocumentRef);
	return document;
}

function validateOpaqueReference(value: string): void {
	if (value.trim() === '') invalidPayload();
}

function mapRequestError(error: unknown): never {
	if (error instanceof DpeInventoryContractError) throw error;
	if (error instanceof ApiError) {
		if (error.status === DPE_INVENTORY_ERROR_STATUS.invalid_reference) {
			throw new DpeInventoryRequestError('invalid_reference');
		}
		if (error.status === DPE_INVENTORY_ERROR_STATUS.refresh_required) {
			throw new DpeInventoryRequestError('refresh_required');
		}
		if (error.status === DPE_INVENTORY_ERROR_STATUS.temporary_unavailable) {
			throw new DpeInventoryRequestError('temporary_unavailable');
		}
		throw new Error('La consultation DPE a échoué.');
	}
	throw new Error('La consultation DPE a échoué.');
}

/** One authenticated, non-cached read of a bounded inventory page. */
export async function getDpeInventory(
	contextRef: string,
	cursor: string | null = null,
	limit = DPE_INVENTORY_DEFAULT_PAGE_SIZE,
	fetchImpl?: typeof fetch
): Promise<DpeInventoryPage> {
	validateOpaqueReference(contextRef);
	if (!Number.isInteger(limit) || limit < 1 || limit > DPE_INVENTORY_MAX_PAGE_SIZE) {
		invalidPayload();
	}
	const query = new URLSearchParams({ contexte: contextRef });
	if (cursor !== null) {
		validateOpaqueReference(cursor);
		query.set('curseur', cursor);
	}
	query.set('limite', String(limit));
	try {
		const payload = await api<unknown>(
			`${DPE_INVENTORY_ROUTE}?${query}`,
			{ cache: 'no-store' },
			fetchImpl
		);
		return projectDpeInventoryPage(payload, contextRef);
	} catch (error) {
		return mapRequestError(error);
	}
}

/** One authenticated, non-cached read of an explicitly requested opaque document. */
export async function getDpeDocument(
	contextRef: string,
	documentRef: string,
	fetchImpl?: typeof fetch
): Promise<DpeConsultedDocument> {
	validateOpaqueReference(contextRef);
	validateOpaqueReference(documentRef);
	const query = new URLSearchParams({
		contexte: contextRef,
		reference: documentRef
	});
	try {
		const payload = await api<unknown>(
			`${DPE_DOCUMENT_ROUTE}?${query}`,
			{ cache: 'no-store' },
			fetchImpl
		);
		return projectDpeConsultedDocument(payload, contextRef, documentRef);
	} catch (error) {
		return mapRequestError(error);
	}
}
