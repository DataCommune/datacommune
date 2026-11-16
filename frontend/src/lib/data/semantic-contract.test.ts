import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve as resolvePath } from 'node:path';
import {
	formatField,
	formatNullableField,
	formatDate,
	displayUnit,
	getFieldContract,
	assertFieldsCovered,
	assertNominalStateAllowed,
	listFieldIds,
	SemanticContractError
} from './formatters';
import registryJson from './fiche-fields.v1.json';
import type { OutOfCoverageReason } from '$lib/types/fiche';

/**
 * Real semantic-contract primitives test (Phase 43, Plan 05 -- promotes the 43-02 offline
 * scaffold). Exercises the formatters.ts module against the real fiche-fields.v1.json
 * registry: fr-FR locale, precision, units, dates, numeric-string coercion, zero and the
 * 5 D-04 states. commune-fiche.test.ts / bien-fiche.test.ts cover the mapper-level wiring
 * (43-05-02).
 */

// fr-FR groups thousands with a narrow no-break space (U+202F, NNBSP) -- not a plain space.
const NNBSP = '\u202f';

describe('audit septembre 2026 — contrat de consommation annuelle', () => {
	it('expose la même unité annuelle dans la valeur et dans l’infobulle', () => {
		const fieldId = 'bien.dpe.conso_5_usages_ep';
		expect(displayUnit(fieldId)).toBe('kWhEP/an');
		expect(formatField(fieldId, 'Consommation annuelle', { state: 'value', value: 12000 }).value)
			.toBe(`12${NNBSP}000 kWhEP/an`);
		expect(getFieldContract(fieldId).plausible_range).toEqual({ min: 0, max: null });
	});
});

describe('formatField() -- fr-FR locale + precision + unit (D-03)', () => {
	it('40.01 renders 40,01 % (fiscalite.taux_tfpb, D-01 sentinel)', () => {
		const row = formatField('fiscalite.taux_tfpb', 'Taux TFPB', { state: 'value', value: 40.01 });
		expect(row).toEqual({
			label: 'Taux TFPB',
			value: '40,01 %',
			fieldId: 'fiscalite.taux_tfpb',
			state: 'value',
			presentation: { state: 'value', measuredZero: false }
		});
	});

	it('renders a legitimate TFPNB rate above 100 without inventing a ceiling', () => {
		const contract = getFieldContract('fiscalite.taux_tfpnb');
		expect(contract.plausible_range).toEqual({ min: 0, max: null });
		const row = formatField('fiscalite.taux_tfpnb', 'Taux TFPNB', {
			state: 'value',
			value: 551.16
		});
		expect(row).toEqual({
			label: 'Taux TFPNB',
			value: '551,16 %',
			fieldId: 'fiscalite.taux_tfpnb',
			state: 'value',
			presentation: { state: 'value', measuredZero: false }
		});
	});

	it('0 is a real value: renders "0 %", never collapsed into an absent branch', () => {
		const row = formatField('fiscalite.taux_tfpb', 'Taux TFPB', { state: 'value', value: 0 });
		expect(row.value).toBe('0 %');
		expect(row.available).not.toBe(false);
	});

	it('caps precision (maximumFractionDigits) without padding trailing zeros', () => {
		// vacance.taux_vacance has precision.fraction_digits = 2.
		expect(
			formatField('vacance.taux_vacance', 'Taux de vacance', { state: 'value', value: 9.4 })
				.value
		).toBe('9,4 %'); // not "9,40 %"
		expect(
			formatField('vacance.taux_vacance', 'Taux de vacance', {
				state: 'value',
				value: 9.4567
			}).value
		).toBe('9,46 %'); // truncated to 2 fraction digits
	});

	it('derives the display unit from the registry, not a per-call literal', () => {
		expect(
			formatField('demographie.population', 'Population', { state: 'value', value: 21179 })
				.value
		).toBe(`21${NNBSP}179 hab.`);
		expect(
			formatField('demographie.densite', 'Densité', { state: 'value', value: 281.4 }).value
		).toBe('281,4 hab./km²');
		expect(
			formatField('demographie.superficie', 'Superficie', { state: 'value', value: 75.27 })
				.value
		).toBe('75,27 km²');
		expect(
			formatField('revenus.revenu_median', 'Revenu médian', { state: 'value', value: 21340 })
				.value
		).toBe(`21${NNBSP}340 €/UC/an`);
	});

	it('year formatter never groups digits (2024, not 2 024)', () => {
		expect(
			formatField('demographie.annee', 'Année', { state: 'value', value: 2024 }).value
		).toBe('2024');
	});

	it('boolean formatter renders Oui/Non', () => {
		expect(
			formatField('zonage_abc.reclassement', 'Reclassement ABC', {
				state: 'value',
				value: true
			}).value
		).toBe('Oui');
		expect(
			formatField('zonage_abc.reclassement', 'Reclassement ABC', {
				state: 'value',
				value: false
			}).value
		).toBe('Non');
	});

	it('text formatter passes strings through unchanged', () => {
		expect(
			formatField('zonage_abc.zone', 'Zone ABC', { state: 'value', value: 'B2' }).value
		).toBe('B2');
	});
});

describe('formatField() -- numeric string coercion (T-43-06, prod resolve/commune contract)', () => {
	it('coerces a numeric-string value exactly like a real number', () => {
		const fromNumber = formatField('bien.dvf.valeur_fonciere', 'Valeur foncière', {
			state: 'value',
			value: 168000
		});
		const fromString = formatField('bien.dvf.valeur_fonciere', 'Valeur foncière', {
			state: 'value',
			value: '168000.00'
		});
		expect(fromString).toEqual(fromNumber);
		expect(fromString.value).toBe(`168${NNBSP}000 €`);
	});

	it('an unparsable numeric string degrades to a hard gap instead of "NaN"', () => {
		const row = formatField('bien.dvf.valeur_fonciere', 'Valeur foncière', {
			state: 'value',
			value: 'not-a-number'
		});
		expect(row.value).toBe('non disponible');
		expect(row.available).toBe(false);
	});
});

describe('formatDate() -- DATA-05 non-future invariant', () => {
	const contract = getFieldContract('bien.dvf.date_mutation');

	it('formats a past date fr-FR', () => {
		expect(formatDate('2023-09-12', contract)).toBe('12/09/2023');
	});

	it('rejects a future date (DATA-05)', () => {
		const future = new Date(Date.now() + 1000 * 60 * 60 * 24 * 365).toISOString();
		expect(() => formatDate(future, contract)).toThrow(SemanticContractError);
	});

	it('rejects an unparsable date', () => {
		expect(() => formatDate('not-a-date', contract)).toThrow(SemanticContractError);
	});

	it('formatField downgrades a future date_mutation to an honest gap, not a crash', () => {
		const future = new Date(Date.now() + 1000 * 60 * 60 * 24 * 30).toISOString();
		const row = formatField('bien.dvf.date_mutation', 'Date de mutation', {
			state: 'value',
			value: future
		});
		// Phase 49 (W-05): the rendered row is a hard gap, so its state is the degraded
		// `temporary_error` — never 'value' on something that was not displayed.
		expect(row).toEqual({
			label: 'Date de mutation',
			value: 'non disponible',
			available: false,
			fieldId: 'bien.dvf.date_mutation',
			state: 'temporary_error',
			presentation: { state: 'temporary_error' }
		});
	});
});

describe('formatField() -- the 5 D-04 states are distinct (T-43-06)', () => {
	const fieldId = 'fiscalite.taux_tfpb';
	const label = 'Taux TFPB';

	it('value -> the formatted value, no available/muted flags', () => {
		expect(formatField(fieldId, label, { state: 'value', value: 40.01 })).toEqual({
			label,
			value: '40,01 %',
			fieldId,
			state: 'value',
			presentation: { state: 'value', measuredZero: false }
		});
	});

	it('not_reported -> muted "Non renseigné" (record exists, this field does not)', () => {
		expect(formatField(fieldId, label, { state: 'not_reported' })).toEqual({
			label,
			value: 'Non renseigné',
			muted: true,
			fieldId,
			state: 'not_reported',
			presentation: { state: 'not_reported' }
		});
	});

	it('out_of_coverage -> hard gap "non disponible"', () => {
		expect(formatField(fieldId, label, { state: 'out_of_coverage' })).toEqual({
			label,
			value: 'non disponible',
			available: false,
			fieldId,
			state: 'out_of_coverage',
			presentation: { state: 'out_of_coverage' }
		});
	});

	it('temporary_error -> hard gap "non disponible" (same rendering, distinct state)', () => {
		expect(formatField(fieldId, label, { state: 'temporary_error' })).toEqual({
			label,
			value: 'non disponible',
			available: false,
			fieldId,
			state: 'temporary_error',
			presentation: { state: 'temporary_error' }
		});
	});

	it('not_applicable -> out of contract on this field, the D-20 guard rejects it', () => {
		// Was: "hard gap non applicable". `fiscalite.taux_tfpb` does NOT list `not_applicable` in
		// its allowed_states — and neither does any other registry entry (see the WR-01 FINDING
		// test below). Emitting it here was an out-of-contract probe; the guard now says so. The
		// wording of the branch itself stays locked by etats-libelles.test.ts (LIBELLES_ETATS).
		expect(getFieldContract(fieldId).allowed_states).not.toContain('not_applicable');
		expect(() => formatField(fieldId, label, { state: 'not_applicable' })).toThrow(
			SemanticContractError
		);
	});

	it('Phase 49 (D-18): out_of_coverage and temporary_error RENDER alike but stay distinguishable', () => {
		const coverage = formatField(fieldId, label, { state: 'out_of_coverage' });
		const error = formatField(fieldId, label, { state: 'temporary_error' });
		expect(coverage.value).toBe(error.value); // same wording for the reader
		expect(coverage.state).not.toBe(error.state); // different reason for the ℹ️
	});

	it('null is never collapsed into zero, error is never collapsed into not_reported', () => {
		const zero = formatField(fieldId, label, { state: 'value', value: 0 });
		const absent = formatField(fieldId, label, { state: 'not_reported' });
		const error = formatField(fieldId, label, { state: 'temporary_error' });
		expect(zero.value).not.toBe(absent.value);
		expect(absent.value).not.toBe(error.value);
		expect(zero.available).not.toBe(false);
	});
});

describe('formatField() -- allowed_states guard (WR-01, doctrine D-20)', () => {
	/**
	 * Fixture choice is contract-driven, not incidental:
	 *  - `commune.nom_departement` allows ["value","not_reported"] -> `out_of_coverage` is a
	 *    NOMINAL state the registry forbids for it.
	 *  - `commune.code_insee` allows ["value"] only -> `not_reported` is likewise forbidden.
	 *  - `bien.dvf.valeur_fonciere` allows ["value","out_of_coverage"] -> `temporary_error` is NOT
	 *    listed, and must still pass (D-20: degradation state, admitted everywhere).
	 */
	it('throws on a NOMINAL state the registry does not allow for that field (dev/test)', () => {
		expect(getFieldContract('commune.nom_departement').allowed_states).not.toContain(
			'out_of_coverage'
		);
		expect(() =>
			formatField('commune.nom_departement', 'Département', { state: 'out_of_coverage' })
		).toThrow(SemanticContractError);
		// The message names the field, the emitted state and the allowed list.
		expect(() =>
			formatField('commune.nom_departement', 'Département', { state: 'out_of_coverage' })
		).toThrow(/commune\.nom_departement.*out_of_coverage.*value, not_reported/s);

		expect(getFieldContract('commune.code_insee').allowed_states).toEqual(['value']);
		expect(() =>
			formatField('commune.code_insee', 'Code INSEE', { state: 'not_reported' })
		).toThrow(SemanticContractError);
	});

	it('D-20: temporary_error is admitted on a field whose registry does not list it', () => {
		const contract = getFieldContract('bien.dvf.valeur_fonciere');
		expect(contract.allowed_states).not.toContain('temporary_error');
		// Saying `out_of_coverage` on a code failure would be a lie: `temporary_error` is the honest
		// state, so it is never checked against allowed_states.
		const row = formatField('bien.dvf.valeur_fonciere', 'Valeur foncière', {
			state: 'temporary_error'
		});
		expect(row).toEqual({
			label: 'Valeur foncière',
			value: 'non disponible',
			available: false,
			fieldId: 'bien.dvf.valeur_fonciere',
			state: 'temporary_error',
			presentation: { state: 'temporary_error' }
		});
	});

	it('the internal value->temporary_error degradation still bypasses the guard', () => {
		// Same field as commune-fiche.test.ts:231-242 (W-05), which must stay green unmodified.
		const row = formatField('bien.dvf.valeur_fonciere', 'Valeur foncière', {
			state: 'value',
			value: 'not-a-number'
		});
		expect(row.state).toBe('temporary_error');
		expect(row.available).toBe(false);
	});

	it('admits `not_applicable` only for the three mathematically justified indicators', () => {
		// Discovered BY the guard, recorded rather than silenced (50-03 SUMMARY → 50-09 register).
		// `not_applicable` belongs to the D-04 closed set and has a glossary label + explanation,
		// but no field of fiche-fields.v1.json lists it in `allowed_states`. It is therefore
		// unreachable through formatField for every registered field — dead branch, dead copy. The
		// registry is NOT widened to hide this: this test is the executable trace of the defect.
		const admettent = listFieldIds().filter((id) =>
			getFieldContract(id).allowed_states.includes('not_applicable')
		);
		expect(admettent).toEqual([
			'dvf.indicateurs.maison.variation_pct',
			'dvf.indicateurs.appartement.variation_pct',
			'dpe.indicateurs.part_fg_pct'
		]);
		expect(() =>
			formatField('demographie.population', 'Population', { state: 'not_applicable' })
		).toThrow(SemanticContractError);
	});

	it('a state listed in allowed_states behaves exactly as before, no exception', () => {
		// `not_available` (D-47): the demographie row is present, the value is not. Its rendering
		// belongs to the fiche lot (03-07); the D-04 states below keep their behaviour.
		expect(getFieldContract('demographie.population').allowed_states).toEqual([
			'value',
			'not_reported',
			'out_of_coverage',
			'temporary_error',
			'not_available'
		]);
		for (const state of ['not_reported', 'out_of_coverage', 'temporary_error'] as const) {
			expect(() =>
				formatField('demographie.population', 'Population', { state })
			).not.toThrow();
		}
		expect(
			formatField('demographie.population', 'Population', { state: 'value', value: 21179 }).value
		).toBe(`21${NNBSP}179 hab.`);
	});

	/**
	 * RW-11 (Phase 50) — the NON-STRICT branch, i.e. the one every reader of the production
	 * bundle runs. It used to `return` in silence, so a contract violation reaching production
	 * left no evidence anywhere. `strict` is passed explicitly because `MODE === 'test'` makes the
	 * module constant true here: without the parameter this branch is unreachable from a test, and
	 * an unreachable branch is an unproven one.
	 */
	it('RW-11: out of contract and NOT strict -> warns naming field and state, never throws', () => {
		const contract = getFieldContract('commune.nom_departement');
		expect(contract.allowed_states).not.toContain('out_of_coverage');

		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		try {
			expect(() => assertNominalStateAllowed(contract, 'out_of_coverage', false)).not.toThrow();
			expect(warn).toHaveBeenCalledTimes(1);
			const message = String(warn.mock.calls[0][0]);
			// The trace must be actionable: which field, which state, and which list it violates.
			expect(message).toContain('[semantic-contract]');
			expect(message).toContain('commune.nom_departement');
			expect(message).toContain('out_of_coverage');
			expect(message).toContain('value, not_reported');
		} finally {
			warn.mockRestore();
		}
	});

	it('RW-11: a compliant state stays SILENT in the non-strict branch (a trace is not noise)', () => {
		const contract = getFieldContract('demographie.population');
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		try {
			// Listed nominal state, plus the degradation state D-20 admits everywhere.
			assertNominalStateAllowed(contract, 'not_reported', false);
			assertNominalStateAllowed(getFieldContract('bien.dvf.valeur_fonciere'), 'temporary_error', false);
			expect(warn).not.toHaveBeenCalled();
		} finally {
			warn.mockRestore();
		}
	});
});

describe('formatNullableField() -- the kvNumber()/kvText() replacement shape', () => {
	it('renders the value when non-null', () => {
		expect(formatNullableField('demographie.population', 'Population', 21179)).toEqual({
			label: 'Population',
			value: `21${NNBSP}179 hab.`,
			fieldId: 'demographie.population',
			state: 'value',
			presentation: { state: 'value', measuredZero: false }
		});
	});

	it('defaults null to the hard out_of_coverage gap', () => {
		expect(formatNullableField('demographie.population', 'Population', null)).toEqual({
			label: 'Population',
			value: 'non disponible',
			available: false,
			fieldId: 'demographie.population',
			state: 'out_of_coverage',
			presentation: { state: 'out_of_coverage' }
		});
	});

	it('an explicit onNull="not_reported" renders the soft muted gap', () => {
		expect(
			formatNullableField(
				'bien.dpe.cout_total_5_usages',
				'Coût énergie estimé',
				null,
				'not_reported'
			)
		).toEqual({
			label: 'Coût énergie estimé',
			value: 'Non renseigné',
			muted: true,
			fieldId: 'bien.dpe.cout_total_5_usages',
			state: 'not_reported',
			presentation: { state: 'not_reported' }
		});
	});
});

describe('displayUnit() -- the single unit authority for the ℹ️ panel (Phase 49, critère « unité »)', () => {
	it('returns the registry suffix, trimmed, for a field that has a unit', () => {
		expect(displayUnit('revenus.revenu_median')).toBe('€/UC/an');
		expect(displayUnit('demographie.population')).toBe('hab.');
		expect(displayUnit('demographie.densite')).toBe('hab./km²');
		expect(displayUnit('loyers.loyer_m2_appartement')).toBe('€/m²/mois');
		expect(displayUnit('vacance.taux_vacance')).toBe('%');
	});

	it('returns null for the unitless contracts (count / year / none), never an empty string', () => {
		// A `count` field: rendering an empty unit line in the ℹ️ would be noise, so the slot is
		// dropped entirely instead.
		expect(displayUnit('demographie.nb_logements')).toBeNull();
		expect(displayUnit('demographie.annee')).toBeNull();
		expect(displayUnit('zonage_abc.zone')).toBeNull();
	});

	it('throws loud on an unregistered field_id, like every other registry accessor (T-43-07)', () => {
		expect(() => displayUnit('this.field.does.not.exist')).toThrow(SemanticContractError);
	});
});

describe('getFieldContract() / assertFieldsCovered() -- T-43-07 mapper-registry drift guard', () => {
	it('throws SemanticContractError on an unknown field_id', () => {
		expect(() => getFieldContract('does.not.exist')).toThrow(SemanticContractError);
		expect(() => formatField('does.not.exist', 'X', { state: 'value', value: 1 })).toThrow(
			SemanticContractError
		);
	});

	it('assertFieldsCovered() passes for real registered ids', () => {
		expect(() =>
			assertFieldsCovered(['fiscalite.taux_tfpb', 'demographie.population'])
		).not.toThrow();
	});

	it('assertFieldsCovered() throws loud, listing every missing field_id', () => {
		expect(() =>
			assertFieldsCovered(['fiscalite.taux_tfpb', 'this.field.does.not.exist'])
		).toThrow(/this\.field\.does\.not\.exist/);
	});

	it('the registry is non-empty and every id is unique (loaded once, cached)', () => {
		const ids = listFieldIds();
		expect(ids.length).toBeGreaterThan(0);
		expect(new Set(ids).size).toBe(ids.length);
	});
});

describe('allowed_out_of_coverage_reasons (D-24, Phase 02-07)', () => {
	// Compile-time half: every published reason must be a member of the TS union, and the
	// `satisfies` below breaks the build if the union loses one of the three.
	const expected = [
		'dataset_non_heberge',
		'territoire_hors_perimetre',
		'source_sans_couverture'
	] as const satisfies readonly OutOfCoverageReason[];

	it('publishes exactly the three reasons, in precedence order, without duplicate', () => {
		const reasons = (registryJson as { allowed_out_of_coverage_reasons?: unknown })
			.allowed_out_of_coverage_reasons;
		expect(reasons).toEqual([...expected]);
		expect(new Set(reasons as string[]).size).toBe(expected.length);
	});

	it('lists the decided states only: a reason is never a new state', () => {
		// The five D-04 states, then the typed absences of D-47 (gate of 2026-09-24, Ianis).
		// Extended by decision, never loosened: a state absent from this list is a contract break.
		expect(registryJson.allowed_states).toEqual([
			'value',
			'not_reported',
			'out_of_coverage',
			'temporary_error',
			'not_applicable',
			'insufficient_sample',
			'not_available',
			'zero_denominator',
			'suppressed',
			'not_qualified'
		]);
		for (const reason of expected) {
			expect(registryJson.allowed_states).not.toContain(reason);
		}
	});
});

describe('copie frontend du contrat (D-51)', () => {
	// The canonical registry lives in contracts/ (repo root); the frontend ships a copy that
	// the backend suite already compares. This guard makes a frontend-only change see a drift
	// too: the copy must stay byte-identical, never a parallel registry.
	const frontendCopyPath = resolvePath(process.cwd(), 'src/lib/data/fiche-fields.v1.json');
	const canonicalPath = resolvePath(process.cwd(), '../contracts/fiche-fields.v1.json');

	it('keeps src/lib/data/fiche-fields.v1.json byte-identical to contracts/fiche-fields.v1.json', () => {
		const frontendCopy = readFileSync(frontendCopyPath);
		const canonical = readFileSync(canonicalPath);
		expect(frontendCopy.length).toBeGreaterThan(0);
		expect(frontendCopy.equals(canonical)).toBe(true);
	});

	it('gives every field of the frontend copy a non-empty description', () => {
		const registry = JSON.parse(readFileSync(frontendCopyPath, 'utf8')) as {
			fields: Array<{ field_id: string; description?: unknown }>;
		};
		expect(registry.fields.length).toBeGreaterThan(0);
		const missing = registry.fields
			.filter((field) => typeof field.description !== 'string' || field.description.trim() === '')
			.map((field) => field.field_id);
		expect(missing).toEqual([]);
	});
});
