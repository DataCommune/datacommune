import type { CommuneSynthese } from '$lib/api/commune';
import type { ResultatMultiResolution } from '$lib/api/resolve';

/**
 * 100% SYNTHETIC golden-case corpus in API shape (Phase 43, Plan 43-08).
 *
 * Every value here is invented: no real residential address, no precise coordinate, no key,
 * no real commune payload. INSEE codes are placeholder in-range identifiers, not resolved
 * against the loaded 44/85 perimeter. The point is to exercise the golden invariants (IDs,
 * non-future dates, ranges, states, units, coherences) by pushing CommuneSynthese /
 * ResultatMultiResolution payloads through the REAL mappers (mapCommuneToFiche /
 * mapBienToFiche) — offline, no network, no component.
 *
 * The REAL corpus (frontend/src/lib/data/__fixtures__/golden-cases.ts) is selected live
 * read-only and frozen by the blocking human checkpoints of Plan 43-09. This synthetic file
 * is its offline stand-in and is never promoted.
 */

export type GoldenCategory =
	| 'complete'
	| 'partial'
	| 'no_dvf'
	| 'no_dpe'
	| 'dataset_out_of_coverage';

interface GoldenCaseBase {
	case_id: string;
	departement: '44' | '85';
	code_insee: string;
	category: GoldenCategory;
	/** ISO date, deliberately in the past so the non-future invariant holds. */
	frozen_at: string;
	/** The commune synthese block (also reused commune-side by the bien mapper). */
	commune: CommuneSynthese;
}

export interface GoldenCommuneCase extends GoldenCaseBase {
	type: 'commune';
}

export interface GoldenAddressCase extends GoldenCaseBase {
	type: 'address';
	/** The /v1/resolve envelope element for the bien mapper. */
	resolve: ResultatMultiResolution;
}

export type GoldenSyntheticCase = GoldenCommuneCase | GoldenAddressCase;

const FROZEN_AT = '2026-07-14';

/** A fully-populated, in-range synthetic commune. Per-category cases null out slices of it. */
function baseCommune(codeInsee: string, overrides: Partial<CommuneSynthese> = {}): CommuneSynthese {
	return {
		code_insee: codeInsee,
		nom_commune: 'Commune Synthétique',
		code_departement: codeInsee.slice(0, 2),
		code_region: '52',
		// demographie
		population: 12000,
		nb_menages: 5200,
		nb_logements: 6100,
		nb_logements_vacants_recensement: 480,
		densite: 210.5,
		superficie: 57.02,
		annee_demographie: 2021,
		// revenus
		revenu_median: 21500,
		taux_pauvrete: 11.2,
		indice_gini: 0.28,
		part_imposee: 52.0,
		annee_revenus: 2021,
		// zonage ABC
		zone_abc: 'B2',
		zone_abc_reclassement: false,
		// vacance
		taux_vacance: 7.4,
		nb_vacants_plus_2ans: 180,
		annee_vacance: 2023,
		// fiscalite
		taux_tfpb: 40.5,
		taux_th_rs: 17.5,
		taux_teom: 11.0,
		nom_epci: 'CC Synthétique',
		annee_fiscalite: 2024,
		// loyers
		loyer_m2_appartement: 9.5,
		loyer_m2_maison: 8.9,
		annee_loyers: 2023,
		// dvf (commune aggregate)
		prix_m2_median: 2450,
		nb_mutations_12m: 120,
		// dpe (commune aggregate)
		dpe_passoires_pct: 6.5,
		dpe_median: 'D',
		annee_construction_mediane: 1988,
		// equipements / scolaire / monuments (commune aggregate)
		nb_equipements: 640,
		nb_etablissements_scolaires: 3,
		nb_monuments: 0, // a real zero — must NOT collapse into a gap (D-04)
		// foncier_pm (commune aggregate)
		nb_proprietaires_pm: 410,
		surface_pm_ha: 320.0,
		// connectivite (commune aggregate)
		fibre_pct: 78.0,
		...overrides
	};
}

/** DVF aggregates nulled → the "no DVF" slice. */
const NO_DVF: Partial<CommuneSynthese> = { prix_m2_median: null, nb_mutations_12m: null };
/** DPE aggregates nulled → the "no DPE" slice. */
const NO_DPE: Partial<CommuneSynthese> = {
	dpe_passoires_pct: null,
	dpe_median: null,
	annee_construction_mediane: null
};

/** A commune outside the loaded perimeter: everything degrades to a gap except its identity. */
function outOfCoverageCommune(codeInsee: string): CommuneSynthese {
	const empty = baseCommune(codeInsee);
	for (const key of Object.keys(empty) as (keyof CommuneSynthese)[]) {
		if (key === 'code_insee' || key === 'nom_commune' || key === 'code_departement') continue;
		empty[key] = null;
	}
	return empty;
}

// --- resolve payload builders (synthetic, no street/precise coordinate kept) ---

function cadastrePayload(codeInsee: string) {
	return {
		id_parcelle: `${codeInsee}-SYNTH-001`,
		commune: codeInsee,
		prefixe: '000',
		section: 'AA',
		numero: '0001',
		contenance: 480
	};
}

function dpePayload() {
	return [
		{
			numero_dpe: 'SYNTH-DPE-001',
			etiquette_dpe: 'D',
			etiquette_ges: 'C',
			surface_habitable: 72,
			annee_construction: 1985,
			periode_construction: '1975-1977',
			type_batiment: 'appartement',
			conso_5_usages_ep: 230,
			emission_ges_5_usages: 20,
			cout_total_5_usages: 980,
			type_energie_principale_chauffage: 'Électricité',
			qualite_isolation_enveloppe: 'moyenne',
			type_installation_chauffage: 'individuel électrique'
		}
	];
}

function dvfPayload(codeInsee: string) {
	return [
		{
			id_mutation: 'SYNTH-DVF-001',
			date_mutation: '2023-09-12',
			valeur_fonciere: 172000,
			type_local: 'Appartement',
			surface_reelle_bati: 72,
			nombre_pieces_principales: 3,
			prix_m2: 2389,
			id_parcelle: `${codeInsee}-SYNTH-001`
		}
	];
}

function risquesPayload(codeInsee: string) {
	return {
		argiles: { codeExposition: '2', exposition: 'Exposition moyenne' },
		icpe: [],
		radon: null,
		risques_naturels: [
			{
				code_insee: codeInsee,
				libelle_commune: 'SYNTHETIQUE',
				risques_detail: [
					{ num_risque: '11', libelle_risque_long: 'Inondation', zone_sismicite: null }
				]
			}
		]
	};
}

function urbanismePayload() {
	return { zones_plu: [{ properties: { libelle: 'Ua', typezone: 'U' } }], prescriptions: [] };
}

function baseResolve(
	codeInsee: string,
	commune: CommuneSynthese,
	datasets: Record<string, unknown>,
	overrides: Partial<ResultatMultiResolution> = {}
): ResultatMultiResolution {
	return {
		type_detecte: 'gps',
		latitude: 0, // explicit null-island sentinel, never a 44/85 location
		longitude: 0,
		adresse: null, // no street kept (D-11)
		id_parcelle: `${codeInsee}-SYNTH-001`,
		commune: 'Commune Synthétique', // NAME, not the INSEE code (Pitfall 4)
		code_postal: '00000',
		score: null,
		datasets: { ...datasets, commune },
		erreurs: [],
		avertissements: [],
		...overrides
	};
}

// --- the corpus: 3 communes + 5 addresses, all five categories across it ---

const communes: GoldenCommuneCase[] = [
	{
		case_id: 'golden-commune-001',
		type: 'commune',
		departement: '44',
		code_insee: '44991',
		category: 'complete',
		frozen_at: FROZEN_AT,
		commune: baseCommune('44991')
	},
	{
		case_id: 'golden-commune-002',
		type: 'commune',
		departement: '85',
		code_insee: '85991',
		category: 'partial',
		frozen_at: FROZEN_AT,
		commune: baseCommune('85991', {
			loyer_m2_appartement: null,
			fibre_pct: null,
			nb_proprietaires_pm: null,
			surface_pm_ha: null
		})
	},
	{
		case_id: 'golden-commune-003',
		type: 'commune',
		departement: '85',
		code_insee: '85992',
		category: 'dataset_out_of_coverage',
		frozen_at: FROZEN_AT,
		commune: outOfCoverageCommune('85992')
	}
];

const addresses: GoldenAddressCase[] = [
	(() => {
		const commune = baseCommune('44993');
		return {
			case_id: 'golden-address-001',
			type: 'address' as const,
			departement: '44' as const,
			code_insee: '44993',
			category: 'complete' as const,
			frozen_at: FROZEN_AT,
			commune,
			resolve: baseResolve('44993', commune, {
				cadastre: cadastrePayload('44993'),
				dpe: dpePayload(),
				dvf: dvfPayload('44993'),
				risques: risquesPayload('44993'),
				urbanisme: urbanismePayload()
			})
		};
	})(),
	(() => {
		const commune = baseCommune('44994');
		return {
			case_id: 'golden-address-002',
			type: 'address' as const,
			departement: '44' as const,
			code_insee: '44994',
			category: 'partial' as const,
			frozen_at: FROZEN_AT,
			commune,
			// partial: DPE + DVF present, cadastre/risques/urbanisme absent → those bien rows degrade.
			resolve: baseResolve('44994', commune, {
				dpe: dpePayload(),
				dvf: dvfPayload('44994')
			})
		};
	})(),
	(() => {
		const commune = baseCommune('85993', NO_DVF);
		return {
			case_id: 'golden-address-003',
			type: 'address' as const,
			departement: '85' as const,
			code_insee: '85993',
			category: 'no_dvf' as const,
			frozen_at: FROZEN_AT,
			commune,
			// dvf requested but empty → its bien rows degrade honestly.
			resolve: baseResolve('85993', commune, {
				cadastre: cadastrePayload('85993'),
				dpe: dpePayload(),
				dvf: [],
				risques: risquesPayload('85993'),
				urbanisme: urbanismePayload()
			})
		};
	})(),
	(() => {
		const commune = baseCommune('85994', NO_DPE);
		return {
			case_id: 'golden-address-004',
			type: 'address' as const,
			departement: '85' as const,
			code_insee: '85994',
			category: 'no_dpe' as const,
			frozen_at: FROZEN_AT,
			commune,
			// dpe requested but empty → its bien rows degrade honestly.
			resolve: baseResolve('85994', commune, {
				cadastre: cadastrePayload('85994'),
				dpe: [],
				dvf: dvfPayload('85994'),
				risques: risquesPayload('85994'),
				urbanisme: urbanismePayload()
			})
		};
	})(),
	(() => {
		const commune = outOfCoverageCommune('85995');
		return {
			case_id: 'golden-address-005',
			type: 'address' as const,
			departement: '85' as const,
			code_insee: '85995',
			category: 'dataset_out_of_coverage' as const,
			frozen_at: FROZEN_AT,
			commune,
			// out of coverage: only the commune block comes back → every bien row degrades.
			resolve: baseResolve('85995', commune, {}, { id_parcelle: null })
		};
	})()
];

export const GOLDEN_SYNTHETIC_CORPUS: GoldenSyntheticCase[] = [...communes, ...addresses];
