import { ApiError, api } from './client';
import {
	DpeInventoryContractError,
	projectDpeAttributionV2,
	projectDpeInventoryPage,
	type DpeAttributionV2,
	type DpeInventoryPage
} from './dpe-inventory';

export const DPE_MATCHING_ROUTE = '/v1/bien/dpe/comparaison';
export function isDpeMatchingPreviewEnvironment(hostname: string, localDev: boolean, explicitOptIn: boolean): boolean {
	if (hostname === 'datacommune.fr' || hostname === 'www.datacommune.fr') return false;
	if (localDev) return hostname === 'localhost' || hostname === '127.0.0.1';
	return hostname === 'dev.datacommune.fr' && explicitOptIn;
}
export type DpePropertyType = 'maison' | 'appartement';
export type DpeSurfaceNature = 'habitable_dpe' | 'carrez' | 'annonce' | 'dvf' | 'autre';
export type DpeCriterionState = 'known' | 'matching' | 'conflicting' | 'unknown' | 'not_applicable' | 'not_comparable';
export type DpeMatchBlocker = 'address_unverified' | 'source_coverage_unknown' | 'source_state_unknown' | 'source_absent' | 'source_deactivated' | 'validity_unknown' | 'validity_expired' | 'validity_inconsistent' | 'validity_future' | 'replacement_unchecked' | 'replacement_replaced' | 'replacement_ambiguous' | 'graph_incoherent' | 'inventory_partial';
export type DpeMatchLimit = 'dwelling_identity_unproven' | 'address_association_unverified' | 'source_coverage_unknown' | 'graph_incoherent' | 'inventory_partial' | 'candidates_outside_page' | 'candidate_count_unknown' | 'surface_has_no_calibrated_threshold';

export interface DpeMatchTarget { readonly type_bien?: DpePropertyType; readonly surface?: { readonly value: number; readonly nature: DpeSurfaceNature } }
export type DpeCriterionComparison =
	| { readonly criterion: 'type_bien'; readonly state: DpeCriterionState; readonly target: DpePropertyType | null; readonly source: DpePropertyType | 'autre' | null }
	| { readonly criterion: 'surface'; readonly state: DpeCriterionState; readonly target: number | null; readonly source: number | null; readonly target_nature: DpeSurfaceNature | null; readonly source_nature: 'habitable_dpe'; readonly delta: number | null };
export interface DpeDocumentComparison { readonly document_ref: string; readonly criteria: readonly [DpeCriterionComparison, DpeCriterionComparison]; readonly blockers: readonly DpeMatchBlocker[] }
export interface DpeMatchResult { readonly inventory: DpeInventoryPage; readonly attribution: DpeAttributionV2; readonly rule_version: 'dpe-factual-comparison-v1'; readonly comparisons: readonly DpeDocumentComparison[]; readonly limits: readonly DpeMatchLimit[] }

type RecordValue = Record<string, unknown>;
const criterionStates: readonly DpeCriterionState[] = ['known', 'matching', 'conflicting', 'unknown', 'not_applicable', 'not_comparable'];
const blockers: readonly DpeMatchBlocker[] = ['address_unverified', 'source_coverage_unknown', 'source_state_unknown', 'source_absent', 'source_deactivated', 'validity_unknown', 'validity_expired', 'validity_inconsistent', 'validity_future', 'replacement_unchecked', 'replacement_replaced', 'replacement_ambiguous', 'graph_incoherent', 'inventory_partial'];
const limits: readonly DpeMatchLimit[] = ['dwelling_identity_unproven', 'address_association_unverified', 'source_coverage_unknown', 'graph_incoherent', 'inventory_partial', 'candidates_outside_page', 'candidate_count_unknown', 'surface_has_no_calibrated_threshold'];
const propertyTypes = ['maison', 'appartement'] as const;
const surfaceNatures = ['habitable_dpe', 'carrez', 'annonce', 'dvf', 'autre'] as const;

export class DpeMatchingContractError extends Error {
	constructor() {
		super('Invalid DPE matching payload.');
		this.name = 'DpeMatchingContractError';
	}
}
export class DpeMatchingRequestError extends Error {
	readonly kind: 'network' | 'invalid_reference' | 'refresh_required' | 'temporary_unavailable';
	constructor(kind: DpeMatchingRequestError['kind']) {
		super(
			kind === 'refresh_required'
				? 'Le contexte DPE doit être actualisé.'
				: 'La comparaison DPE est momentanément indisponible.'
		);
		this.name = 'DpeMatchingRequestError';
		this.kind = kind;
	}
}
const invalid = (): never => {
	throw new DpeMatchingContractError();
};
function record(value: unknown): RecordValue {
	return value !== null && typeof value === 'object' && !Array.isArray(value)
		? value as RecordValue
		: invalid();
}
function exact(input: RecordValue, keys: readonly string[]): void {
	if (Object.keys(input).length !== keys.length || keys.some((key) => !Object.hasOwn(input, key))) {
		invalid();
	}
}
function literal<T extends string>(value: unknown, allowed: readonly T[]): T {
	return typeof value === 'string' && allowed.includes(value as T) ? value as T : invalid();
}
function nullableLiteral<T extends string>(value: unknown, allowed: readonly T[]): T | null {
	return value === null ? null : literal(value, allowed);
}
function nullableFinite(value: unknown): number | null {
	if (value === null) return null;
	if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return invalid();
	return value;
}
function uniqueList<T extends string>(value: unknown, allowed: readonly T[]): T[] {
	if (!Array.isArray(value)) return invalid();
	const entries: unknown[] = value;
	const out = entries.map((entry: unknown) => literal(entry, allowed));
	return new Set(out).size === out.length ? out : invalid();
}

function projectCriterion(value: unknown): DpeCriterionComparison {
	const input = record(value);
	const criterion = literal(input.criterion, ['type_bien', 'surface'] as const);
	const state = literal(input.state, criterionStates);
	if (criterion === 'type_bien') {
		exact(input, ['criterion', 'state', 'target', 'source']);
		const target = nullableLiteral(input.target, propertyTypes);
		const source = nullableLiteral(input.source, [...propertyTypes, 'autre'] as const);
		const valid = state === 'not_applicable' ? target === null : state === 'unknown' ? target !== null && source === null : state === 'matching' ? target !== null && source === target : state === 'conflicting' ? target !== null && source !== null && source !== target : false;
		if (!valid) invalid();
		return { criterion, state, target, source };
	}
	exact(input, ['criterion', 'state', 'target', 'source', 'target_nature', 'source_nature', 'delta']);
	const target = nullableFinite(input.target), source = nullableFinite(input.source), target_nature = nullableLiteral(input.target_nature, surfaceNatures), source_nature = literal(input.source_nature, ['habitable_dpe'] as const);
	const delta = input.delta === null ? null : typeof input.delta === 'number' && Number.isFinite(input.delta) ? input.delta : invalid();
	const comparable = state === 'known' || state === 'matching';
	const valid = comparable ? target !== null && source !== null && target_nature === 'habitable_dpe' && delta === source - target && ((state === 'matching') === (delta === 0)) : state === 'not_applicable' ? target === null && target_nature === null && delta === null : state === 'unknown' ? target !== null && target_nature === 'habitable_dpe' && source === null && delta === null : state === 'not_comparable' ? target !== null && target_nature !== null && target_nature !== 'habitable_dpe' && delta === null : false;
	if (!valid) invalid();
	return { criterion, state, target, source, target_nature, source_nature, delta };
}

export function projectDpeMatchResult(value: unknown, expectedContext: string): DpeMatchResult {
	const input = record(value);
	exact(input, ['inventory', 'attribution', 'rule_version', 'comparisons', 'limits']);
	let inventory: DpeInventoryPage;
	let attribution: DpeAttributionV2;
	try {
		inventory = projectDpeInventoryPage(input.inventory, expectedContext);
		attribution = projectDpeAttributionV2(input.attribution);
	} catch (error) {
		if (error instanceof DpeInventoryContractError) invalid();
		throw error;
	}
	const rawComparisons = input.comparisons;
	if (!Array.isArray(rawComparisons)) return invalid();
	const comparisons: DpeDocumentComparison[] = rawComparisons.map((raw: unknown): DpeDocumentComparison => {
		const item = record(raw);
		exact(item, ['document_ref', 'criteria', 'blockers']);
		const documentRef = item.document_ref;
		const rawCriteria = item.criteria;
		if (typeof documentRef !== 'string' || !documentRef.trim() || !Array.isArray(rawCriteria) || rawCriteria.length !== 2) return invalid();
		const projected = rawCriteria.map((criterion: unknown) => projectCriterion(criterion));
		if (projected[0]?.criterion !== 'type_bien' || projected[1]?.criterion !== 'surface') return invalid();
		return { document_ref: documentRef, criteria: projected as [DpeCriterionComparison, DpeCriterionComparison], blockers: uniqueList(item.blockers, blockers) };
	});
	if (comparisons.map(x => x.document_ref).join('\0') !== inventory.items.map(x => x.document_ref).join('\0') || new Set(comparisons.map(x => x.document_ref)).size !== comparisons.length) invalid();
	return {
		inventory,
		attribution,
		rule_version: literal(input.rule_version, ['dpe-factual-comparison-v1'] as const),
		comparisons,
		limits: uniqueList(input.limits, limits)
	};
}

export function createDpeMatchingClient(fetchImpl?: typeof fetch) {
	let generation = 0;
	let controller: AbortController | null = null;
	return {
		invalidate() {
			generation++;
			controller?.abort();
			controller = null;
		},
		async compare(context: string, target: DpeMatchTarget = {}, cursor?: string | null, limit = 20): Promise<DpeMatchResult | null> {
			const current = ++generation;
			controller?.abort();
			controller = new AbortController();
			const body: RecordValue = { contexte: context, limite: limit, cible: target };
			if (cursor) body.curseur = cursor;
			try {
				const payload = await api<unknown>(DPE_MATCHING_ROUTE, {
					method: 'POST', cache: 'no-store', signal: controller.signal, body: JSON.stringify(body)
				}, fetchImpl);
				return current === generation ? projectDpeMatchResult(payload, context) : null;
			} catch (error) {
				const aborted = typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError';
				if (current !== generation || aborted) return null;
				if (error instanceof DpeMatchingContractError) throw error;
				if (error instanceof ApiError) {
					const kind = error.status === 400
						? 'invalid_reference'
						: error.status === 409 ? 'refresh_required' : 'temporary_unavailable';
					throw new DpeMatchingRequestError(kind);
				}
				throw new DpeMatchingRequestError('network');
			}
		}
	};
}
