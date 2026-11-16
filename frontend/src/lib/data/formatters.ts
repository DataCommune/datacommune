// Single formatting authority for the Fiche fields (Phase 43, D-05). Pure module — zero Svelte
// imports. Driven by the versioned semantic-contract registry (`fiche-fields.v1.json`, D-03),
// kept byte-identical to `contracts/fiche-fields.v1.json` by
// `api/backend/scripts/sync_semantic_contract.py`. `commune-fiche.ts` and `bien-fiche.ts` both
// consume `formatField`/`formatNullableField` instead of maintaining duplicated FR_NUMBER/
// kvNumber/kvText/kvGap helpers (T-43-07: a mapper field without a registered field_id fails
// loud instead of silently formatting an unverified value).

import type { KvRow, FieldValue } from '$lib/types/fiche';
import registryJson from './fiche-fields.v1.json';
import { LIBELLES_ECARTS_HISTORIQUES } from './etats-libelles';

// --- registry types (mirrors app/common/semantic_contract.py's FieldContract, D-03) ---

export type Formatter =
	| 'number'
	| 'percent_points'
	| 'currency'
	| 'area_km2'
	| 'density'
	| 'date'
	| 'year'
	| 'boolean'
	| 'text';

/** D-04 closed set — mirrors `FieldValue['state']` (`ALLOWED_STATES` on the backend). */
export type SemanticState =
	| 'value'
	| 'not_reported'
	| 'out_of_coverage'
	| 'temporary_error'
	| 'not_applicable';

export interface FieldContract {
	field_id: string;
	value_type: 'string' | 'integer' | 'decimal' | 'boolean';
	unit: string;
	precision: { fraction_digits: number | null };
	plausible_range: { min: number | null; max: number | null };
	zero_semantics: 'measured_zero' | 'implausible_zero' | 'not_applicable';
	formatter: Formatter;
	allowed_states: SemanticState[];
	description?: string;
}

interface Registry {
	contract_version: string;
	fields: FieldContract[];
}

const REGISTRY = registryJson as Registry;

/** Raised on a registry-contract violation: unknown field_id, malformed value, DATA-05 breach. */
export class SemanticContractError extends Error {}

let byId: Map<string, FieldContract> | null = null;

function fieldsById(): Map<string, FieldContract> {
	if (byId == null) {
		byId = new Map(REGISTRY.fields.map((f) => [f.field_id, f]));
	}
	return byId;
}

/** All field_id known to the registry — the coverage assertion surface (mirrors list_field_ids). */
export function listFieldIds(): string[] {
	return [...fieldsById().keys()];
}

/**
 * DOCTRINE D-20 — what `allowed_states` means, and why one state escapes it (WR-01, Phase 50).
 *
 * `allowed_states` enumerates a field's NOMINAL states: the states that describe the DATA itself
 * (the value exists, the record exists but not this field, the commune is out of coverage, the
 * notion does not apply). It is a data contract, declared per field in `fiche-fields.v1.json`.
 *
 * `temporary_error` is NOT a nominal state — it is the DEGRADATION state, structurally admitted on
 * EVERY field regardless of its list. It means "the code broke here, now": a formatter threw, a
 * dataset is in `erreurs[]`. Forcing such a field back onto one of its nominal states would make
 * the ℹ️ panel tell the reader "hors périmètre couvert" about a bug — a lie. So the guard below
 * never checks `temporary_error`, and the internal `value → temporary_error` degradation of
 * `formatField` (a value that fails its own formatter invariant) crosses the module untouched.
 *
 * STRICT IN DEV/TEST, TRACED IN PRODUCTION — and the second half is NOT the same as "enforced".
 * On violation the guard throws `SemanticContractError` — same fail-closed posture as
 * `getFieldContract` right below — but ONLY when `import.meta.env.DEV` or `MODE === 'test'`. In
 * production the state still goes through to the reader: an uncaught exception would turn a
 * contract defect into a broken page, which is exactly what the "honest degradation" doctrine
 * forbids. The defect must break the build for the developer, never the Fiche for the reader
 * (threat register T-50-06 / T-50-07).
 *
 * What production does instead is EMIT A TRACE (`console.warn`) and continue. It used to return in
 * silence, which meant the one data-driven path into this guard (`datasetGapState`, `bien-fiche`)
 * could go out of contract for every reader with no observable evidence anywhere (RW-11, Phase 50).
 * A trace is not enforcement and does not pretend to be: it is the difference between a defect that
 * leaves a footprint in a browser console and one that leaves none at all.
 *
 * COROLLARY: a nominal state emitted out of contract is a MAPPER defect, not a registry that is
 * too narrow. The fix is the call site (or a recorded entry in the phase defect register), never a
 * widening of `fiche-fields.v1.json` to make the guard quiet.
 */
const STRICT_CONTRACT_MODE: boolean = import.meta.env.DEV || import.meta.env.MODE === 'test';

/**
 * Reads `allowed_states` — until Phase 50 the field was declared, populated for the 72 registry
 * entries, and compared by NO executable code (fail-open on the one contract it advertised).
 *
 * `strict` defaults to the module mode and exists as a parameter for ONE reason: the non-strict
 * branch is the branch every reader actually runs, and a branch no test can enter is a branch
 * nobody can prove. No production call site passes it — `formatField` calls this with two
 * arguments, exactly as before.
 */
export function assertNominalStateAllowed(
	contract: FieldContract,
	state: SemanticState,
	strict: boolean = STRICT_CONTRACT_MODE
): void {
	if (state === 'temporary_error') return; // D-20: degradation state, admitted everywhere.
	if (contract.allowed_states.includes(state)) return;
	const violation =
		`[${contract.field_id}] nominal state "${state}" violates allowed_states ` +
		`[${contract.allowed_states.join(', ')}] (D-20).`;
	if (!strict) {
		// Production: TRACE, then let the state through (see the doctrine block above). The message
		// names the field and the offending state, because a trace nobody can act on is noise.
		console.warn(`[semantic-contract] ${violation}`);
		return;
	}
	throw new SemanticContractError(
		`${violation} Fix the mapper call site — do not widen ` +
			`fiche-fields.v1.json to silence this.`
	);
}

/** Typed access to a single field contract. Throws when the field_id is not registered (T-43-07). */
export function getFieldContract(fieldId: string): FieldContract {
	const contract = fieldsById().get(fieldId);
	if (contract == null) {
		throw new SemanticContractError(
			`Unknown field_id (missing from fiche-fields.v1.json): "${fieldId}"`
		);
	}
	return contract;
}

/**
 * D-03 coverage assertion: every field_id a mapper actually references must exist in the
 * registry. Mirrors `app.common.semantic_contract.assert_fields_covered` on the backend — a new
 * Fiche field wired without a registry entry is a build-time bug, not a runtime degradation.
 */
export function assertFieldsCovered(fieldIds: Iterable<string>): void {
	const known = fieldsById();
	const missing = [...new Set(fieldIds)].filter((id) => !known.has(id));
	if (missing.length > 0) {
		throw new SemanticContractError(
			`Mapper references field_id(s) missing from fiche-fields.v1.json: ${missing.join(', ')}`
		);
	}
}

// --- unit → display suffix (D-05: the registry's `unit` drives the text, never a per-call-site
// literal like the old kvNumber(label, value, '€/m²/mois') pattern) ---

const UNIT_SUFFIXES: Record<string, string> = {
	none: '',
	count: '',
	year: '',
	person: ' hab.',
	person_per_km2: ' hab./km²',
	km2: ' km²',
	m2: ' m²',
	// 04-06: height of the building of the point.
	metre: ' m',
	hectare: ' ha',
	euro: ' €',
	euro_per_uc_year: ' €/UC/an',
	euro_per_m2: ' €/m²',
	euro_per_m2_month: ' €/m²/mois',
	euro_per_year: ' €/an',
	kwh_per_m2_year: ' kWh/m²/an',
	kwh_ep_per_year: ' kWhEP/an',
	// 04-04: yearly emissions of a DPE document (total of the document, not per square metre).
	kg_co2_eq_per_year: ' kg CO₂ éq/an',
	// 04.1-12 (C51): per-square-metre values published by the ADEME for a DPE document.
	kwh_ep_per_m2_per_year: ' kWhEP/m²/an',
	kg_co2_eq_per_m2_per_year: ' kg CO₂ éq/m²/an',
	percent_point: ' %',
	percent: ' %'
};

function unitSuffix(unit: string): string {
	return UNIT_SUFFIXES[unit] ?? '';
}

/**
 * Phase 49 (UI-07, ℹ️ "unité" slot) — the SINGLE authority on how a field's unit is written out.
 * Same doctrine as D-05: the registry's `unit` drives the text, a call site never spells out
 * "€/m²/mois" by hand. Returns `null` for the unitless contracts (`none`/`count`/`year`) so the
 * ℹ️ panel can drop the slot entirely instead of rendering an empty line.
 * Throws (T-43-07) on an unregistered field_id, like every other registry accessor here.
 */
export function displayUnit(fieldId: string): string | null {
	return unitSuffix(getFieldContract(fieldId).unit).trim() || null;
}

// --- numeric coercion (T-43-06: a value that fails to coerce fails loud, never becomes NaN) ---

/**
 * The resolve/commune endpoints serialize Postgres `numeric` columns as JSON strings
 * (e.g. "168000.00") — defense-in-depth alongside each mapper's own readNumber() coercion
 * (43-PATTERNS landmine 10).
 */
function coerceNumeric(value: number | string, contract: FieldContract): number {
	if (typeof value === 'number') {
		if (!Number.isFinite(value)) {
			throw new SemanticContractError(`[${contract.field_id}] non-finite numeric value`);
		}
		return value;
	}
	const n = Number(value);
	if (!Number.isFinite(n)) {
		throw new SemanticContractError(
			`[${contract.field_id}] unparsable numeric value: "${value}"`
		);
	}
	return n;
}

const FR_LOCALE = 'fr-FR';

/**
 * fr-FR grouped number, capped at the contract's precision (D-03). Only `maximumFractionDigits`
 * is set — never `minimumFractionDigits` — so `9.4` stays `"9,4"` (no padding to `"9,40"`); the
 * contract's `fraction_digits` caps runaway precision without inventing trailing zeros.
 */
function formatNumeric(value: number | string, contract: FieldContract): string {
	const n = coerceNumeric(value, contract);
	const fractionDigits = contract.precision.fraction_digits;
	return new Intl.NumberFormat(FR_LOCALE, {
		maximumFractionDigits: fractionDigits ?? 3
	}).format(n);
}

/**
 * Bare fr-FR number, contract-precision-capped, no unit suffix — for `KpiItem.value` where the
 * unit renders in its own component slot (unlike KvRow's inline-unit convention). Callers keep
 * their own null-branch (the KPI fallback text is contextual), this only formats a known value.
 */
export function formatBareNumber(fieldId: string, value: number | string): string {
	return formatNumeric(value, getFieldContract(fieldId));
}

/** Years render raw ("2024"), never grouped ("2 024") — replaces the ad hoc String(annee). */
function formatYear(value: number | string, contract: FieldContract): string {
	return String(Math.trunc(coerceNumeric(value, contract)));
}

function formatBooleanValue(value: boolean): string {
	return value ? 'Oui' : 'Non';
}

/**
 * DATA-05 invariant: a Fiche date can never be in the future — a future `date_mutation`,
 * `annee_construction`, etc. signals a data bug, not a real observation. Throws
 * `SemanticContractError` on an unparsable or future date; `formatField` downgrades that into
 * an honest gap row instead of rendering a falsified date.
 */
export function formatDate(value: string, contract: FieldContract): string {
	const parsed = new Date(value);
	if (Number.isNaN(parsed.getTime())) {
		throw new SemanticContractError(`[${contract.field_id}] unparsable date: "${value}"`);
	}
	if (parsed.getTime() > Date.now()) {
		throw new SemanticContractError(
			`[${contract.field_id}] future date rejected (DATA-05 non-future invariant): "${value}"`
		);
	}
	return parsed.toLocaleDateString(FR_LOCALE);
}

/** Formatter dispatch — the only place a `formatter` enum value maps to a rendering function. */
function formatByContract(contract: FieldContract, value: string | number | boolean): string {
	switch (contract.formatter) {
		case 'number':
			return `${formatNumeric(value as number | string, contract)}${unitSuffix(contract.unit)}`;
		case 'percent_points':
			return `${formatNumeric(value as number | string, contract)} %`;
		case 'currency':
			return `${formatNumeric(value as number | string, contract)}${unitSuffix(contract.unit)}`;
		case 'area_km2':
			return `${formatNumeric(value as number | string, contract)} km²`;
		case 'density':
			return `${formatNumeric(value as number | string, contract)} hab./km²`;
		case 'year':
			return formatYear(value as number | string, contract);
		case 'boolean':
			return formatBooleanValue(value as boolean);
		case 'date':
			return formatDate(value as string, contract);
		case 'text':
			return String(value);
		default: {
			const exhaustive: never = contract.formatter;
			throw new SemanticContractError(`Unknown formatter: ${String(exhaustive)}`);
		}
	}
}

// Phase 49 (D-10): the state wordings are no longer literals here — they come from the single
// state glossary. Pure relocation of the strings, byte-identical, zero rendering change.
const HARD_GAP_TEXT = LIBELLES_ECARTS_HISTORIQUES.out_of_coverage;

/**
 * Single formatting authority for both mappers (D-05, key link fiche-fields.v1.json → this
 * module → commune-fiche.ts/bien-fiche.ts). Looks up `fieldId` in the registry (throws —
 * T-43-07 — if the mapper references an unregistered field), then renders `state` into a KvRow:
 *   - `value`            → the formatted value (finite, never invented — T-43-06).
 *   - `not_reported`      → soft/muted "Non renseigné" (record exists, this field doesn't — D-03,
 *                           replaces the old kvSourceNull()).
 *   - `out_of_coverage`   → hard gap, available:false, "non disponible" (replaces kvGap() / the
 *                           old kvNumber()/kvText() null branch).
 *   - `temporary_error`   → hard gap, available:false, "non disponible" (dataset in erreurs[]).
 *   - `not_applicable`    → hard gap, available:false, "non applicable".
 * A `value` state that fails its own formatter invariant (e.g. the DATA-05 future-date check)
 * degrades to the same hard-gap treatment instead of throwing through to the mapper/UI.
 *
 * Phase 49 (D-18): every returned row carries its `fieldId` AND its exact semantic `state`.
 * Attaching them HERE — the one place every mapper row goes through — is what makes divergence
 * impossible by construction: no call site can forget the ℹ️ key, and the three visually
 * identical hard gaps stay distinguishable by their reason.
 */
export function formatField(fieldId: string, label: string, state: FieldValue): KvRow {
	const contract = getFieldContract(fieldId);
	// D-20 (see the doctrine next to getFieldContract): the NOMINAL state received is compared to
	// the registry BEFORE rendering. `temporary_error` is exempt, and the degradation produced
	// inside `case 'value'` below never comes back through here.
	assertNominalStateAllowed(contract, state.state);

	switch (state.state) {
		case 'value':
			try {
				const value = formatByContract(contract, state.value);
				const numeric = contract.value_type === 'integer' || contract.value_type === 'decimal';
				return {
					label, value, fieldId, state: 'value',
					presentation: {
						state: 'value',
						measuredZero: numeric && contract.zero_semantics === 'measured_zero' &&
							coerceNumeric(state.value as number | string, contract) === 0
					}
				};
			} catch (err) {
				if (err instanceof SemanticContractError) {
					// The value exists but broke its own formatting invariant (unparsable number,
					// DATA-05 future date…). What is RENDERED is a hard gap, so the row must carry a
					// degraded state: `temporary_error` — the value failed here and now, and a later
					// refresh may well fix it. Keeping state 'value' would make the ℹ️ show the
					// definition of a figure that was never displayed, with no reason for its absence.
					return {
						label,
						value: HARD_GAP_TEXT,
						available: false,
						fieldId,
						state: 'temporary_error',
						presentation: { state: 'temporary_error' }
					};
				}
				throw err;
			}
		case 'not_reported':
			return {
				label,
				value: LIBELLES_ECARTS_HISTORIQUES.not_reported,
				muted: true,
				fieldId,
				state: 'not_reported',
				presentation: { state: 'not_reported' }
			};
		case 'out_of_coverage':
			return {
				label,
				value: LIBELLES_ECARTS_HISTORIQUES.out_of_coverage,
				available: false,
				fieldId,
				state: 'out_of_coverage',
				presentation: { state: 'out_of_coverage' }
			};
		case 'temporary_error':
			return {
				label,
				value: LIBELLES_ECARTS_HISTORIQUES.temporary_error,
				available: false,
				fieldId,
				state: 'temporary_error',
				presentation: { state: 'temporary_error' }
			};
		case 'not_applicable':
			return {
				label,
				value: LIBELLES_ECARTS_HISTORIQUES.not_applicable,
				available: false,
				fieldId,
				state: 'not_applicable',
				presentation: { state: 'not_applicable' }
			};
		default: {
			const exhaustive: never = state;
			throw new SemanticContractError(`Unknown D-04 state: ${String(exhaustive)}`);
		}
	}
}

/**
 * Ergonomic wrapper around `formatField` for the common "read a nullable value, degrade on
 * null" mapper shape (the old `kvNumber(label, v, unit)` / `kvText(label, v)` call sites).
 * `onNull` defaults to the hard gap (`out_of_coverage`); pass `'not_reported'` for the softer
 * "record exists, this field doesn't" treatment (the old kvSourceNull() call sites).
 */
export function formatNullableField(
	fieldId: string,
	label: string,
	value: string | number | boolean | null,
	onNull: Exclude<SemanticState, 'value'> = 'out_of_coverage'
): KvRow {
	return value == null
		? formatField(fieldId, label, { state: onNull })
		: formatField(fieldId, label, { state: 'value', value });
}
