// Shared Fiche contract — consumed by the fiche mappers and the components they feed.
// Source of truth: ../mockups/COMPOSANT-L0L1L2.md (props) + Phase 8 D-01 (locked verdict palette).

/**
 * Verdict palette — LOCKED (D-01 Phase 8). Exactly 4 variants, never invent a 5th.
 *
 * Phase 49 D-04 — DOCUMENTARY amendment (the union itself is untouched): the 4 variants no
 * longer encode an editorial opinion computed from in-house cut-offs, they encode a POSITION
 * relative to a PUBLISHED national referential (`$lib/data/referentiels.ts`):
 *   good    → above / on the favourable side of the published reference
 *   neutral → close to the reference, inside its documented tolerance band (D-06)
 *   watch   → below / on the side the reference itself flags (e.g. passoire énergétique)
 *   info    → contextual, no judgement carried — including the "no comparable published
 *             referential exists" regime (D-07) and the missing-data fallback (D9-09)
 * A metric with no published referential simply carries no `verdictLabel`: honest silence, never
 * a 5th variant.
 */
export type Verdict = 'good' | 'neutral' | 'watch' | 'info';

/** Host fiche perimeter (D-08). Commune fiche is 'commune'; 'bien' lands in Phase 11. */
export type Scope = 'bien' | 'commune';

/**
 * A single KPI chip in the L0 synthese grid.
 *
 * Phase 49 (D-05) ADDITIVE extensions — never a fork of this type:
 *   - `verdictLabel` : short verbal position ("Au-dessus de la médiane"). ABSENT means no chip
 *     label at all — the D-07 honest-silence regime, not an empty string.
 *   - `fieldId`      : semantic-contract key (same key space as fiche-fields.v1.json), the ℹ️
 *     lookup key. Pure identifiers (parcelle, code INSEE…) deliberately carry none (D-15).
 *   - `ecartTexte`   : the full numeric-gap sentence (value + tolerance band + millésime +
 *     source) rendered by the ℹ️ panel, never in the chip itself.
 */
export interface KpiItem {
	/** Explicit stable key for non-registry concepts; never used for registry lookup. */
	id?: string;
	presentation?: FieldPresentation;
	label: string;
	value: string | number;
	unit?: string;
	trend?: 'up' | 'flat';
	verdict: Verdict;
	verdictLabel?: string;
	fieldId?: string;
	ecartTexte?: string | null;
	primary?: boolean;
	dpeClass?: string;
}

/** A label/value row in a Theme body (table.kv). `available: false` → "non disponible".
 *  `muted: true` → value is real (e.g. "Non renseigné") but rendered with the same soft/gap
 *  marker style as available:false — distinct from structural absence of the record (D-03).
 *
 *  Phase 49 (D-18) ADDITIVE extensions, attached by `formatField` itself so no call site can
 *  forget them:
 *   - `fieldId` : the ℹ️ lookup key.
 *   - `state`   : the EXACT semantic state of the row. `out_of_coverage`, `temporary_error` and
 *     `not_applicable` all render identically ("non disponible"/"non applicable") but they are
 *     three different REASONS for the absence — the ℹ️ must be able to tell them apart, so the
 *     state travels with the row instead of being re-derived (and re-guessed) downstream. */
export interface KvRow {
	id?: string;
	presentation?: FieldPresentation;
	/** Source context is exported/rendered, but is not a numeric field or an indicator. */
	kind?: 'context';
	label: string;
	value: string;
	available?: boolean;
	muted?: boolean;
	fieldId?: string;
	state?: FieldValue['state'];
	/**
	 * Dotted path of the value in a /v1/commune row (`revenu_median`,
	 * `indicateurs_dvf.residentiel_maison.courante.mediane`…), the same key as the `cle` of a
	 * criterion served by /v1/communes/comparaison (D-48, D-51). Set by the projections on every
	 * value row; context rows and rows of a value the API does not serve carry none.
	 */
	valuePath?: string;
}

/**
 * D-04 discriminated value state (Phase 43, T-43-06). Exactly the 5 canonical states —
 * `value`, `not_reported`, `out_of_coverage`, `temporary_error`, `not_applicable` — never a
 * bare `null`/`undefined` standing in for absence. TypeScript enforces that `.value` is only
 * reachable when `.state === 'value'`: an absent/error branch can never carry (or invent) a
 * value. `null`, zero and error are never interchangeable — a `measured_zero` field reports
 * `{ state: 'value', value: 0 }`, never collapsed into an absent branch. Consumed by
 * `$lib/data/formatters.ts` (`formatField`), the single formatting authority shared by
 * `commune-fiche.ts` and `bien-fiche.ts` (D-05).
 */
export type FieldValue =
	| { state: 'value'; value: string | number | boolean }
	| { state: 'not_reported' }
	| { state: 'out_of_coverage'; motif?: OutOfCoverageReason }
	| { state: 'temporary_error' }
	| { state: 'not_applicable' };

/**
 * Why a value is `out_of_coverage` (D-24, Phase 02-07) — the exact list published as
 * `allowed_out_of_coverage_reasons` in fiche-fields.v1.json. A reason qualifies the state; it is
 * never a fifth state. Optional: absent when the API did not state one.
 */
export type OutOfCoverageReason =
	| 'dataset_non_heberge'
	| 'territoire_hors_perimetre'
	| 'source_sans_couverture';

/** A supplied explanation, never inferred from NULL, a threshold or a formatted string. */
export interface QualifiedAbsenceReason {
	code: 'zero_denominator' | 'insufficient_sample' | 'suppressed' | 'source_reason';
	label: string;
	explanation: string;
	evidence: string;
	effectif?: number;
}

/** Public presentation evidence. Absent states cannot carry the underlying numeric value. */
export type FieldPresentation = {
	grain?: string;
	reserves?: readonly string[];
} & (
	| { state: 'value'; measuredZero?: boolean }
	| { state: Exclude<FieldValue['state'], 'value'>; reason?: QualifiedAbsenceReason }
);

/** A themed accordion unit (L1 body + L2 footer). The 5 themes are always present. */
export interface ThemeModel {
	key: string;
	emoji: string;
	name: string;
	hint: string;
	rows: KvRow[];
	/**
	 * rowsBien / rowsCommune feed the scope='bien' split « Sur ce bien » / « Dans la commune »
	 * (FICHE-06). The scope='commune' fiche uses only `rows` (these stay undefined there).
	 */
	rowsBien?: KvRow[];
	rowsCommune?: KvRow[];
	sources: string;
}
