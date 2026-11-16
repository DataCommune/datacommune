import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';
import { mapCommuneToFiche } from './commune-fiche';
import { mapBienToFiche } from './bien-fiche';
import { getFieldContract } from './formatters';
import {
	GOLDEN_SYNTHETIC_CORPUS,
	type GoldenSyntheticCase,
	type GoldenCommuneCase,
	type GoldenAddressCase
} from './__fixtures__/golden-cases.synthetic';
import type { DpePublicDiagnostic } from '$lib/api/resolve';
import type { DpeAttributionV2 } from '$lib/api/dpe-inventory';
import type { DpeFicheSelection } from './dpe-attribution-view';

/**
 * Offline scaffold: golden-cases manifest validator (Phase 43, Plan 02).
 *
 * Mirrors api/backend/tests/test_golden_cases_contract.py — proves the
 * D-10/D-11 validation rules on a fully synthetic manifest, no network, no
 * component, no real case selected (Plan 43-09 is the blocking human
 * checkpoint for that). Plan 43-08 replaces the local validator below with
 * the real `golden-cases.synthetic.ts` fixture and schema-driven checks.
 */

const ALLOWED_CASE_TYPES = new Set(['commune', 'address']);
const OPAQUE_CASE_ID_RE = /^golden-(commune|address)-\d{3}$/;

// D-10 categories. 43-09 amendment: 'partial' stays allowed but is
// unobservable on the 44/85 live surface, so coverage only requires four.
const ALLOWED_CATEGORIES = new Set([
	'complete',
	'partial',
	'no_dvf',
	'no_dpe',
	'dataset_out_of_coverage'
]);
const REQUIRED_CATEGORIES = new Set([
	'complete',
	'no_dvf',
	'no_dpe',
	'dataset_out_of_coverage'
]);

const REQUIRED_KEYS = ['case_id', 'type', 'category', 'frozen_at', 'invariants'] as const;

// D-11: a manifest entry must never carry a raw residential address or a key.
const FORBIDDEN_KEYS = new Set(['adresse', 'address', 'api_key', 'secret']);

type CaseEntry = Record<string, unknown>;

/** Validate a single golden-case manifest entry. Diagnostics are case_id-scoped. */
function validateCaseEntry(entry: CaseEntry): string[] {
	const errors: string[] = [];
	const caseId = typeof entry.case_id === 'string' ? entry.case_id : '(unknown case_id)';

	const missing = REQUIRED_KEYS.filter((key) => !(key in entry));
	if (missing.length > 0) {
		errors.push(`[${caseId}] missing required keys: ${missing.join(', ')}`);
	}

	const forbidden = [...FORBIDDEN_KEYS].filter((key) => key in entry);
	if (forbidden.length > 0) {
		errors.push(`[${caseId}] D-11 forbidden PII/secret keys present: ${forbidden.join(', ')}`);
	}

	if (entry.type !== undefined && !ALLOWED_CASE_TYPES.has(String(entry.type))) {
		errors.push(`[${caseId}] unknown case type '${String(entry.type)}'`);
	}
	if (typeof entry.case_id !== 'string' || !OPAQUE_CASE_ID_RE.test(entry.case_id)) {
		errors.push(`[${caseId}] case_id does not match opaque pattern`);
	}

	if (entry.category !== undefined && !ALLOWED_CATEGORIES.has(String(entry.category))) {
		errors.push(`[${caseId}] unknown category '${String(entry.category)}'`);
	}

	return errors;
}

/** Validate manifest cardinality (D-10: 3-5 communes, 5-10 addresses) + coverage. */
function validateManifest(entries: CaseEntry[]): string[] {
	const errors: string[] = [];
	const seen = new Set<string>();

	for (const entry of entries) {
		const caseId = entry.case_id;
		if (typeof caseId === 'string') {
			if (seen.has(caseId)) {
				errors.push(`duplicate case_id: ${caseId}`);
			} else {
				seen.add(caseId);
			}
		}
		errors.push(...validateCaseEntry(entry));
	}

	const communes = entries.filter((e) => e.type === 'commune');
	const addresses = entries.filter((e) => e.type === 'address');

	if (communes.length < 3 || communes.length > 5) {
		errors.push(`expected 3-5 communes, got ${communes.length}`);
	}
	if (addresses.length < 5 || addresses.length > 10) {
		errors.push(`expected 5-10 addresses, got ${addresses.length}`);
	}

	const observedCategories = new Set(entries.map((e) => e.category));
	const missingCategories = [...REQUIRED_CATEGORIES].filter((c) => !observedCategories.has(c));
	if (missingCategories.length > 0) {
		errors.push(`missing required categories: ${missingCategories.join(', ')}`);
	}

	return errors;
}

function validCase(overrides: CaseEntry = {}): CaseEntry {
	return {
		case_id: 'golden-commune-001',
		type: 'commune',
		category: 'complete',
		frozen_at: '2026-07-14',
		invariants: { code_insee: '44991' },
		...overrides
	};
}

/** 3 communes + 5 addresses covering all five D-10 categories. */
function minimalValidManifest(): CaseEntry[] {
	const communes = [
		validCase({ case_id: 'golden-commune-001', category: 'complete' }),
		validCase({ case_id: 'golden-commune-002', category: 'partial' }),
		validCase({ case_id: 'golden-commune-003', category: 'dataset_out_of_coverage' })
	];
	const addresses = [
		validCase({ case_id: 'golden-address-001', type: 'address', category: 'complete' }),
		validCase({ case_id: 'golden-address-002', type: 'address', category: 'partial' }),
		validCase({ case_id: 'golden-address-003', type: 'address', category: 'no_dvf' }),
		validCase({ case_id: 'golden-address-004', type: 'address', category: 'no_dpe' }),
		validCase({
			case_id: 'golden-address-005',
			type: 'address',
			category: 'dataset_out_of_coverage'
		})
	];
	return [...communes, ...addresses];
}

describe('golden-cases manifest validator (offline scaffold)', () => {
	it('accepts a valid synthetic case', () => {
		expect(validateCaseEntry(validCase())).toEqual([]);
	});

	it('rejects a missing required key', () => {
		const entry = validCase();
		delete entry.frozen_at;
		expect(validateCaseEntry(entry).some((e) => e.includes('missing required keys'))).toBe(true);
	});

	it('rejects a non-opaque case_id', () => {
		const errors = validateCaseEntry(validCase({ case_id: 'c1' }));
		expect(errors.some((e) => e.includes('opaque pattern'))).toBe(true);
	});

	it('rejects a raw address key (synthetic invalid fixture, D-11)', () => {
		const errors = validateCaseEntry(validCase({ adresse: '12 rue de la Paix, Nantes' }));
		expect(errors.some((e) => e.includes('D-11 forbidden PII/secret keys'))).toBe(true);
	});

	it('rejects a category outside the D-10 closed set', () => {
		const errors = validateCaseEntry(validCase({ category: 'invented_category' }));
		expect(errors.some((e) => e.includes('unknown category'))).toBe(true);
	});

	it('accepts a minimal valid manifest (3 communes + 5 addresses, 5 categories)', () => {
		expect(validateManifest(minimalValidManifest())).toEqual([]);
	});

	it('rejects a manifest with too few communes', () => {
		const manifest = minimalValidManifest().filter((e) => e.type !== 'commune');
		manifest.push(validCase({ case_id: 'c1', category: 'complete' }));
		const errors = validateManifest(manifest);
		expect(errors.some((e) => e.includes('expected 3-5 communes'))).toBe(true);
	});

	it('accepts a manifest without the partial category (43-09 amendment)', () => {
		const manifest = minimalValidManifest().map((e) =>
			e.category === 'partial' ? { ...e, category: 'complete' as const } : e
		);
		expect(validateManifest(manifest)).toEqual([]);
	});

	it('rejects a manifest missing a required category', () => {
		const manifest = minimalValidManifest().filter((e) => e.category !== 'no_dpe');
		const errors = validateManifest(manifest);
		expect(errors.some((e) => e.includes('missing required categories'))).toBe(true);
	});

	it('rejects a manifest with a duplicate case_id', () => {
		const manifest = minimalValidManifest();
		manifest.push({ ...manifest[0] });
		const errors = validateManifest(manifest);
		expect(errors.some((e) => e.includes('duplicate case_id'))).toBe(true);
	});
});

/**
 * Plan 43-08 — synthetic API-shape corpus traversed to the REAL mappers.
 *
 * The scaffold above validates a manifest in the abstract. This block proves the golden
 * invariants (IDs, non-future dates, ranges, states, units, coherences) on 100% synthetic
 * API-shape payloads (CommuneSynthese / ResultatMultiResolution) pushed THROUGH
 * mapCommuneToFiche / mapBienToFiche — no network, no component, no real case. The real
 * corpus (golden-cases.ts) is frozen by the blocking human checkpoints of Plan 43-09.
 */

const CASE_ID_RE = /^golden-(commune|address)-\d{3}$/;
const INSEE_RE = /^(44|85)\d{3}$/;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function isNonFutureIsoDate(iso: unknown): boolean {
	if (typeof iso !== 'string' || !ISO_DATE_RE.test(iso)) return false;
	const t = new Date(`${iso}T00:00:00Z`).getTime();
	return !Number.isNaN(t) && t <= Date.now();
}

/** null passes (a gap is not a range violation); a present value must sit in [min, max]. */
function inRange(v: number | null | undefined, min: number, max: number): string | null {
	if (v == null) return null;
	return v >= min && v <= max ? null : `out of [${min}, ${max}]: ${v}`;
}

/**
 * Golden invariant validator (IDs, dates, ranges, coherences, category↔payload consistency).
 * Returns a list of case_id-scoped diagnostics; [] means the case is coherent.
 */
function validateGoldenCase(c: GoldenSyntheticCase): string[] {
	const errors: string[] = [];
	const id = c.case_id;
	const tag = (m: string) => errors.push(`[${id}] ${m}`);

	// --- IDs ---
	if (!CASE_ID_RE.test(id)) tag(`case_id does not match opaque pattern`);
	if (!id.includes(c.type)) tag(`case_id type segment != ${c.type}`);
	if (!INSEE_RE.test(c.code_insee)) tag(`code_insee ${c.code_insee} not a 44/85 INSEE code`);
	if (!c.code_insee.startsWith(c.departement)) tag(`code_insee vs departement mismatch`);

	// --- dates (non-future) ---
	if (!isNonFutureIsoDate(c.frozen_at)) tag(`frozen_at ${c.frozen_at} invalid or in the future`);

	// --- commune-level ranges + coherences ---
	const k = c.commune;
	const pctFields: [string, number | null][] = [
		['taux_vacance', k.taux_vacance],
		['dpe_passoires_pct', k.dpe_passoires_pct],
		['fibre_pct', k.fibre_pct],
		['taux_pauvrete', k.taux_pauvrete],
		['part_imposee', k.part_imposee]
	];
	for (const [name, v] of pctFields) {
		const err = inRange(v, 0, 100);
		if (err) tag(`${name} ${err}`);
	}
	for (const [fieldId, name, value] of [
		['fiscalite.taux_tfpb', 'taux_tfpb', k.taux_tfpb],
		['fiscalite.taux_th_rs', 'taux_th_rs', k.taux_th_rs],
		['fiscalite.taux_teom', 'taux_teom', k.taux_teom]
	] as [string, string, number | null][]) {
		if (value == null) continue;
		const { min, max } = getFieldContract(fieldId).plausible_range;
		if ((min != null && value < min) || (max != null && value > max)) {
			tag(`${name} out of [${min ?? '-∞'}, ${max ?? '+∞'}]: ${value}`);
		}
	}
	const gini = inRange(k.indice_gini, 0, 1);
	if (gini) tag(`indice_gini ${gini}`);
	for (const [name, v] of [
		['population', k.population],
		['revenu_median', k.revenu_median],
		['prix_m2_median', k.prix_m2_median],
		['densite', k.densite]
	] as [string, number | null][]) {
		if (v != null && v < 0) tag(`${name} is negative: ${v}`);
	}

	if (k.nb_menages != null && k.population != null && k.nb_menages > k.population) {
		tag(`nb_menages (${k.nb_menages}) > population (${k.population})`);
	}
	if (
		k.nb_logements_vacants_recensement != null &&
		k.nb_logements != null &&
		k.nb_logements_vacants_recensement > k.nb_logements
	) {
		tag(`vacant logements > total logements`);
	}

	// --- category ↔ payload consistency ---
	// Commune cases only: an address case's category describes the bien-level
	// dataset states (selector `_categorize`), and its commune context block
	// legitimately keeps commune-wide DVF/DPE aggregates.
	if (c.type === 'commune') {
		if (c.category === 'no_dvf' && (k.prix_m2_median != null || k.nb_mutations_12m != null)) {
			tag(`category no_dvf but commune carries DVF aggregates`);
		}
		if (c.category === 'no_dpe' && (k.dpe_median != null || k.dpe_passoires_pct != null)) {
			tag(`category no_dpe but commune carries DPE aggregates`);
		}
	}

	// --- address-only: resolve payload dates + parcelle id ---
	if (c.type === 'address') {
		const dvf = (c.resolve.datasets?.dvf ?? []) as Record<string, unknown>[];
		for (const row of Array.isArray(dvf) ? dvf : []) {
			const dm = row.date_mutation;
			if (dm != null && !isNonFutureIsoDate(dm)) tag(`dvf date_mutation ${dm} invalid or future`);
		}
		const parcelle = c.resolve.id_parcelle;
		if (parcelle != null && !parcelle.startsWith(c.code_insee)) {
			tag(`id_parcelle ${parcelle} not prefixed by code_insee ${c.code_insee}`);
		}
	}

	return errors;
}

const communeCases = GOLDEN_SYNTHETIC_CORPUS.filter(
	(c): c is GoldenCommuneCase => c.type === 'commune'
);
const addressCases = GOLDEN_SYNTHETIC_CORPUS.filter(
	(c): c is GoldenAddressCase => c.type === 'address'
);

function rowByLabel(rows: { label: string; value: unknown; available?: boolean }[], re: RegExp) {
	return rows.find((r) => re.test(r.label));
}

describe('golden synthetic corpus — cardinality & coverage (D-10)', () => {
	it('has 3–5 communes and 5–10 addresses', () => {
		expect(communeCases.length).toBeGreaterThanOrEqual(3);
		expect(communeCases.length).toBeLessThanOrEqual(5);
		expect(addressCases.length).toBeGreaterThanOrEqual(5);
		expect(addressCases.length).toBeLessThanOrEqual(10);
	});

	it('covers all five D-10 categories across the corpus', () => {
		const observed = new Set(GOLDEN_SYNTHETIC_CORPUS.map((c) => c.category));
		for (const cat of ['complete', 'partial', 'no_dvf', 'no_dpe', 'dataset_out_of_coverage']) {
			expect(observed.has(cat as GoldenSyntheticCase['category'])).toBe(true);
		}
	});

	it('has unique, opaque case_ids', () => {
		const ids = GOLDEN_SYNTHETIC_CORPUS.map((c) => c.case_id);
		expect(new Set(ids).size).toBe(ids.length);
		expect(ids.every((id) => CASE_ID_RE.test(id))).toBe(true);
	});
});

describe('golden synthetic corpus — every case is coherent (IDs, dates, ranges, coherences)', () => {
	for (const c of GOLDEN_SYNTHETIC_CORPUS) {
		it(`${c.case_id} satisfies every golden invariant`, () => {
			expect(validateGoldenCase(c)).toEqual([]);
		});
	}
});

describe('golden synthetic corpus — commune API-shape traverses mapCommuneToFiche', () => {
	for (const c of communeCases) {
		it(`${c.case_id} (${c.category}) maps to 4 KPI + 5 themes without throwing`, () => {
			const { kpi, themes } = mapCommuneToFiche(c.commune);
			expect(kpi).toHaveLength(4);
			expect(themes.map((t) => t.key)).toEqual([
				'marche',
				'energie',
				'risques',
				'environnement',
				'propriete'
			]);
		});

		it(`${c.case_id} (${c.category}) renders category-consistent states`, () => {
			const { themes } = mapCommuneToFiche(c.commune);
			const env = themes.find((t) => t.key === 'environnement')!;
			const marche = themes.find((t) => t.key === 'marche')!;
			const energie = themes.find((t) => t.key === 'energie')!;
			const popRow = rowByLabel(env.rows, /population/i);
			const prixRow = rowByLabel(marche.rows, /prix médian de vente/i);
			const passoiresRow = rowByLabel(energie.rows, /part des classes f et g/i);

			if (c.category === 'complete') {
				// A present field renders with its registry unit; a gap never sneaks in as a value.
				expect(popRow!.available).not.toBe(false);
				expect(String(popRow!.value)).toMatch(/hab\./);
				expect(prixRow!.available).not.toBe(false);
			}
			if (c.category === 'no_dvf') expect(prixRow!.available).toBe(false);
			if (c.category === 'no_dpe') expect(passoiresRow!.available).toBe(false);
			if (c.category === 'dataset_out_of_coverage') {
				// Population absent → hard gap, not an invented value.
				expect(popRow!.available).toBe(false);
			}
		});
	}

	it('a zero-valued count is a real value, never collapsed to a gap (états, D-04)', () => {
		const complete = communeCases.find((c) => c.category === 'complete')!;
		const { themes } = mapCommuneToFiche(complete.commune);
		const monuments = rowByLabel(themes.find((t) => t.key === 'risques')!.rows, /monuments/i);
		expect(monuments!.available).not.toBe(false);
		expect(String(monuments!.value)).toContain('0');
	});
});

/**
 * Phase 51: the Fiche receives the AUTHORISED diagnostic, it no longer picks one out of the
 * payload. The synthetic corpus predates the attribution contract, so the selection is rebuilt
 * here from the case's own DPE payload — the case still describes what the backend knows about
 * the address, only the door it comes through changed. A case whose DPE block is empty yields
 * the closed selection, which is exactly the `no_dpe`/out-of-coverage expectation below.
 */
function selectionDpeDuCas(c: GoldenAddressCase): DpeFicheSelection {
	const bloc = c.resolve.datasets?.dpe;
	const ligne = Array.isArray(bloc) ? (bloc[0] as Partial<DpePublicDiagnostic> | undefined) : undefined;
	if (ligne == null) return { diagnostic: null, gapState: 'out_of_coverage' };
	return {
		diagnostic: {
			numero_dpe: '',
			etage: null,
			batiment: null,
			porte: null,
			type_batiment: null,
			surface_habitable: null,
			etiquette_dpe: null,
			etiquette_ges: null,
			annee_construction: null,
			periode_construction: null,
			type_installation_chauffage: null,
			type_energie_principale_chauffage: null,
			conso_5_usages_ep: null,
			emission_ges_5_usages: null,
			cout_total_5_usages: null,
			qualite_isolation_enveloppe: null,
			date_etablissement_dpe: null,
			date_fin_validite_dpe: null,
			annee_diagnostic: null,
			perime: false,
			...ligne
		},
		gapState: null
	};
}

describe('golden synthetic corpus — address API-shape traverses mapBienToFiche', () => {
	for (const c of addressCases) {
		it(`${c.case_id} (${c.category}) maps to KPI + 5 themes without throwing`, () => {
			const { kpi, themes } = mapBienToFiche(c.resolve, c.commune, selectionDpeDuCas(c));
			expect(kpi.length).toBeGreaterThan(0);
			expect(themes).toHaveLength(5);
		});

		it(`${c.case_id} (${c.category}) degrades per-dataset honestly`, () => {
			const { themes } = mapBienToFiche(c.resolve, c.commune, selectionDpeDuCas(c));
			const marche = themes.find((t) => t.key === 'marche')!;
			const energie = themes.find((t) => t.key === 'energie')!;
			const propriete = themes.find((t) => t.key === 'propriete')!;
			const dvfBien = marche.rowsBien ?? [];
			const dpeBien = energie.rowsBien ?? [];
			const cadastreBien = propriete.rowsBien ?? [];

			if (c.category === 'complete') {
				expect(dvfBien.some((r) => r.available !== false)).toBe(true);
				expect(dpeBien.some((r) => r.available !== false)).toBe(true);
			}
			if (c.category === 'no_dvf') expect(dvfBien.every((r) => r.available === false)).toBe(true);
			if (c.category === 'no_dpe') expect(dpeBien.every((r) => r.available === false)).toBe(true);
			if (c.category === 'partial') {
				// cadastre absent → propriété bien rows degrade, DPE still renders.
				expect(cadastreBien.every((r) => r.available === false)).toBe(true);
				expect(dpeBien.some((r) => r.available !== false)).toBe(true);
			}
			if (c.category === 'dataset_out_of_coverage') {
				const allBien = themes.flatMap((t) => t.rowsBien ?? []);
				expect(allBien.length).toBeGreaterThan(0);
				expect(allBien.every((r) => r.available === false)).toBe(true);
			}
		});
	}
});

describe('golden synthetic corpus — invalidation of incoherent cases', () => {
	const validCommune = () => structuredClone(communeCases.find((c) => c.category === 'complete')!);
	const validAddress = () => structuredClone(addressCases.find((c) => c.category === 'complete')!);

	it('rejects a future frozen_at (dates non-future)', () => {
		const bad = validCommune();
		bad.frozen_at = '2999-01-01';
		expect(validateGoldenCase(bad).some((e) => e.includes('frozen_at'))).toBe(true);
	});

	it('rejects a code_insee outside 44/85 (IDs)', () => {
		const bad = validCommune();
		bad.code_insee = '75056';
		expect(validateGoldenCase(bad).some((e) => e.includes('code_insee'))).toBe(true);
	});

	it('rejects nb_menages greater than population (coherences)', () => {
		const bad = validCommune();
		bad.commune.nb_menages = (bad.commune.population ?? 0) + 5000;
		expect(validateGoldenCase(bad).some((e) => e.includes('nb_menages'))).toBe(true);
	});

	it('rejects an out-of-range percentage (ranges)', () => {
		const bad = validCommune();
		bad.commune.taux_vacance = 150;
		expect(validateGoldenCase(bad).some((e) => e.includes('taux_vacance'))).toBe(true);
	});

	it('rejects an out-of-range Gini index (ranges)', () => {
		const bad = validCommune();
		bad.commune.indice_gini = 1.5;
		expect(validateGoldenCase(bad).some((e) => e.includes('indice_gini'))).toBe(true);
	});

	it('rejects a negative non-negative metric (ranges)', () => {
		const bad = validCommune();
		bad.commune.population = -1;
		expect(validateGoldenCase(bad).some((e) => e.includes('population'))).toBe(true);
	});

	it('rejects vacant logements greater than total logements (coherences)', () => {
		const bad = validCommune();
		bad.commune.nb_logements_vacants_recensement = (bad.commune.nb_logements ?? 0) + 1;
		expect(validateGoldenCase(bad).some((e) => e.includes('vacant logements'))).toBe(true);
	});

	it('rejects a no_dvf case that still carries DVF aggregates (category↔payload)', () => {
		const bad = validCommune();
		bad.category = 'no_dvf';
		// commune still has prix_m2_median from the complete base → inconsistent.
		expect(validateGoldenCase(bad).some((e) => e.includes('no_dvf'))).toBe(true);
	});

	it('rejects a no_dpe case that still carries DPE aggregates (category↔payload)', () => {
		const bad = validCommune();
		bad.category = 'no_dpe';
		expect(validateGoldenCase(bad).some((e) => e.includes('no_dpe'))).toBe(true);
	});

	it('rejects a parcel identifier that does not match the commune', () => {
		const bad = validAddress();
		bad.resolve.id_parcelle = '85999-SYNTH-001';
		expect(validateGoldenCase(bad).some((e) => e.includes('id_parcelle'))).toBe(true);
	});

	it('rejects a future DVF date_mutation on an address case (dates non-future)', () => {
		const bad = validAddress();
		const dvf = bad.resolve.datasets!.dvf as Record<string, unknown>[];
		dvf[0].date_mutation = '2999-12-31';
		expect(validateGoldenCase(bad).some((e) => e.includes('date_mutation'))).toBe(true);
	});

	it('the real mapper degrades a future DVF date to a gap instead of crashing', () => {
		const bad = validAddress();
		const future = new Date(Date.now() + 1000 * 60 * 60 * 24 * 30).toISOString();
		const dvf = bad.resolve.datasets!.dvf as Record<string, unknown>[];
		dvf[0].date_mutation = future;
		const { themes } = mapBienToFiche(bad.resolve, bad.commune);
		const marche = themes.find((t) => t.key === 'marche')!;
		const dateRow = (marche.rowsBien ?? []).find((r) => /date de mutation/i.test(r.label));
		expect(dateRow).toBeTruthy();
		expect(dateRow!.available).toBe(false);
	});
});

/**
 * Phase 58.1 imports the synthetic public fixture in normal CI for its V2 DPE contract.
 * The older full manifest replay remains opt-in through DC_GOLDEN_REAL_FIXTURE=1.
 */

describe('golden real corpus — DPE V2 non-attribution contract', () => {
	it('migrates diagnostic-bearing cases without losing negative evidence', async () => {
		const modulePath = './__fixtures__/golden-cases.ts';
		const loaded = (await import(/* @vite-ignore */ modulePath)) as {
			GOLDEN_REAL_CORPUS?: Array<{
				case_id: string;
				type: string;
				carrier_for?: string[];
				invariants: {
					dpe?: Record<string, unknown>;
				};
				resolve?: {
					dpe_attribution?: Record<string, unknown> | null;
				};
			}>;
		};
		const addresses = loaded.GOLDEN_REAL_CORPUS!.filter((entry) => entry.type === 'address');
		const byId = new Map(addresses.map((entry) => [entry.case_id, entry]));
		expect(
			Object.fromEntries(
				addresses.map((entry) => [
					entry.case_id,
					entry.invariants.dpe?.dpe_inventory_presence
				])
			)
		).toEqual({
			'golden-address-001': 'present',
			'golden-address-002': 'absent',
			'golden-address-003': 'absent',
			'golden-address-004': 'absent',
			'golden-address-005': 'absent',
			'golden-address-007': 'present',
			'golden-address-009': 'absent',
			'golden-address-011': 'present'
		});

		for (const caseId of ['golden-address-001', 'golden-address-007', 'golden-address-011']) {
			const goldenCase = byId.get(caseId)!;
			expect(goldenCase.resolve?.dpe_attribution).toMatchObject({
				version: 2,
				status: 'unassigned',
				reason: 'dwelling_identity_unproven',
				diagnostic: null,
				candidates: []
			});
			expect(goldenCase.invariants.dpe?.dpe_primary_diagnostic_present).toBe(false);
		}

		const antiNeighborCases = addresses.filter((entry) =>
			entry.carrier_for?.includes('dpe_nearer_neighbor_negative')
		);
		expect(antiNeighborCases.length).toBeGreaterThan(0);
		for (const goldenCase of antiNeighborCases) {
			expect(goldenCase.invariants.dpe?.dpe_inventory_presence).toBe('absent');
			expect(goldenCase.invariants.dpe?.dpe_nearby_diagnostic_present).toBe(true);
			expect(goldenCase.resolve?.dpe_attribution?.status).toBe('absent');
		}

		const carriers = new Set(addresses.flatMap((entry) => entry.carrier_for ?? []));
		expect(carriers.has('dpe_exact')).toBe(false);
		expect(carriers.has('dpe_unassigned')).toBe(true);
		const serialized = JSON.stringify(addresses);
		expect(serialized).not.toContain('"status":"exact"');
		expect(serialized).not.toContain('"numero_dpe"');
		expect(serialized).not.toContain('"identifiant_ban"');
		expect(serialized).not.toContain('"etage"');
		expect(serialized).not.toContain('"porte"');
	});
});

const realFixtureEnabled = process.env.DC_GOLDEN_REAL_FIXTURE === '1';

function assertCorpusShapeAndMappers(corpus: GoldenSyntheticCase[]): void {
	const communes = corpus.filter((c): c is GoldenCommuneCase => c.type === 'commune');
	const addresses = corpus.filter((c): c is GoldenAddressCase => c.type === 'address');
	expect(communes.length).toBeGreaterThanOrEqual(3);
	expect(communes.length).toBeLessThanOrEqual(5);
	expect(addresses.length).toBeGreaterThanOrEqual(5);
	expect(addresses.length).toBeLessThanOrEqual(10);
	// Set<string>: REQUIRED_CATEGORIES infers Set<string>, so .has() below
	// rejects GoldenCategory-typed elements otherwise.
	const observedCategories = new Set<string>(corpus.map((c) => c.category));
	expect([...observedCategories].filter((c) => !ALLOWED_CATEGORIES.has(c))).toEqual([]);
	expect([...REQUIRED_CATEGORIES].filter((c) => !observedCategories.has(c))).toEqual([]);
	expect(new Set(corpus.map((c) => c.case_id)).size).toBe(corpus.length);

	for (const goldenCase of corpus) {
		expect(validateGoldenCase(goldenCase), goldenCase.case_id).toEqual([]);
		if (goldenCase.type === 'commune') {
			const fiche = mapCommuneToFiche(goldenCase.commune);
			expect(fiche.kpi, goldenCase.case_id).toHaveLength(4);
			expect(fiche.themes, goldenCase.case_id).toHaveLength(5);
		} else {
			const fiche = mapBienToFiche(goldenCase.resolve, goldenCase.commune);
			expect(fiche.kpi.length, goldenCase.case_id).toBeGreaterThan(0);
			expect(fiche.themes, goldenCase.case_id).toHaveLength(5);
		}
	}
}

describe.skipIf(!realFixtureEnabled)('golden real corpus — protected mapper replay', () => {
	it('matches the manifest case_ids and traverses the real mappers', async () => {
		const modulePath = './__fixtures__/golden-cases.ts';
		type GoldenRealCase = GoldenSyntheticCase & {
			source_millesimes: Record<string, string>;
			invariants: Record<string, unknown>;
			carrier_for?: string[];
		};
		const loaded = (await import(/* @vite-ignore */ modulePath)) as {
			GOLDEN_REAL_CORPUS?: GoldenRealCase[];
		};
		expect(Array.isArray(loaded.GOLDEN_REAL_CORPUS)).toBe(true);
		const corpus = loaded.GOLDEN_REAL_CORPUS!;

		const manifestPath = resolve(
			process.cwd(),
			'../contracts/golden-cases/v1/manifest.json'
		);
		const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as {
			cases: Array<{
				case_id: string;
				type: string;
				departement: string;
				code_insee: string;
				category: string;
				frozen_at: string;
				source_millesimes: Record<string, string>;
				invariants: Record<string, unknown>;
				carrier_for?: string[];
			}>;
		};
		expect(corpus.map((c) => c.case_id).sort()).toEqual(
			manifest.cases.map((c) => c.case_id).sort()
		);
		const manifestById = new Map(manifest.cases.map((c) => [c.case_id, c]));
		for (const goldenCase of corpus) {
			const expected = manifestById.get(goldenCase.case_id)!;
			expect({
				type: goldenCase.type,
				departement: goldenCase.departement,
				code_insee: goldenCase.code_insee,
				category: goldenCase.category,
				frozen_at: goldenCase.frozen_at,
				source_millesimes: goldenCase.source_millesimes,
				invariants: goldenCase.invariants,
				carrier_for: goldenCase.carrier_for
			}).toEqual({
				type: expected.type,
				departement: expected.departement,
				code_insee: expected.code_insee,
				category: expected.category,
				frozen_at: expected.frozen_at,
				source_millesimes: expected.source_millesimes,
				invariants: expected.invariants,
				carrier_for: expected.carrier_for
			});

			if (goldenCase.type !== 'address') continue;
			const attribution = goldenCase.resolve.dpe_attribution as unknown as
				| DpeAttributionV2
				| null
				| undefined;
			expect(attribution, `${goldenCase.case_id}: dpe_attribution`).toBeTruthy();
			const fiche = mapBienToFiche(goldenCase.resolve, goldenCase.commune);
			const dpeKpi = fiche.kpi.find((item) => item.label === 'Étiquette DPE')!;
			const carriers = new Set(goldenCase.carrier_for ?? []);
			const dpeInvariant = (goldenCase.invariants.dpe ?? {}) as Record<string, unknown>;

			if (attribution!.status === 'unassigned') {
				expect(attribution!.version, goldenCase.case_id).toBe(2);
				expect(attribution!.reason, goldenCase.case_id).toBe(
					'dwelling_identity_unproven'
				);
				expect(attribution!.diagnostic, goldenCase.case_id).toBeNull();
				expect(attribution!.candidates, goldenCase.case_id).toEqual([]);
				expect(dpeKpi.value, goldenCase.case_id).toBe('n.d.');
				expect(dpeKpi.dpeClass, goldenCase.case_id).toBeUndefined();
			}

			if (carriers.has('dpe_unassigned')) {
				expect(dpeInvariant.dpe_inventory_presence, goldenCase.case_id).toBe('present');
				expect(attribution!.status, goldenCase.case_id).toBe('unassigned');
				expect(attribution!.building_context, goldenCase.case_id).toBeNull();
			}
			if (carriers.has('dpe_absent')) {
				expect(dpeInvariant.dpe_inventory_presence, goldenCase.case_id).toBe('absent');
				expect(attribution!.status, goldenCase.case_id).toBe('absent');
			}
			if (carriers.has('dpe_multiple_or_building')) {
				expect(dpeInvariant.dpe_inventory_presence, goldenCase.case_id).toBe('present');
				expect(attribution!.status, goldenCase.case_id).toBe('unassigned');
				expect(attribution!.building_context?.count, goldenCase.case_id).toBe(4);
			}
			if (carriers.has('dpe_nearer_neighbor_negative')) {
				expect(dpeInvariant.dpe_inventory_presence, goldenCase.case_id).toBe('absent');
				expect(dpeInvariant.dpe_nearby_diagnostic_present, goldenCase.case_id).toBe(true);
				expect(attribution!.status, goldenCase.case_id).toBe('absent');
			}
		}

		const addresses = corpus.filter((c) => c.type === 'address');
		const observedCarriers = new Set(addresses.flatMap((c) => c.carrier_for ?? []));
		expect(observedCarriers).toEqual(
			new Set([
				'dpe_unassigned',
				'dpe_nearer_neighbor_negative',
				'dpe_absent'
			])
		);
		expect(new Set(addresses.map((c) => c.departement))).toEqual(new Set(['44', '85']));

		for (const goldenCase of addresses) {
			expect(goldenCase.resolve.adresse, goldenCase.case_id).toBeNull();
			expect(goldenCase.resolve.ban_id, goldenCase.case_id).toBeUndefined();
			expect(goldenCase.resolve.latitude, goldenCase.case_id).toBe(0);
			expect(goldenCase.resolve.longitude, goldenCase.case_id).toBe(0);
		}
		const serialized = JSON.stringify(corpus);
		for (const forbiddenKey of [
			'identifiant_ban',
			'adresse_exacte',
			'dpe_nearby_diagnostic',
			'distance_m',
			'api_key',
			'secret',
			'token'
		]) {
			expect(serialized, forbiddenKey).not.toContain(`"${forbiddenKey}"`);
		}
		assertCorpusShapeAndMappers(corpus);
	});
});
