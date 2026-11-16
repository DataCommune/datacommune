import type { CommuneSynthese } from '$lib/api/commune';
import type { ResultatMultiResolution } from '$lib/api/resolve';
import type { DpeAttributionV2 } from '$lib/api/dpe-inventory';
import type { GoldenCategory, GoldenSyntheticCase } from './golden-cases.synthetic';

type GoldenCarrier =
	| 'dpe_unassigned'
	| 'dpe_nearer_neighbor_negative'
	| 'dpe_absent'
	| 'dpe_multiple_or_building';

type GoldenDpeInvariants = {
	dpe_inventory_presence: 'present' | 'absent' | 'unknown';
	dpe_attribution_status: 'unassigned' | 'absent' | 'error';
	dpe_primary_diagnostic_present: boolean;
	dpe_truncated: boolean;
	dpe_nearby_diagnostic_present: boolean;
	dpe_candidate_count: number;
	dpe_building_count: number;
};

type GoldenInvariants = {
	db: Record<string, boolean>;
	api: Record<string, string>;
	mapper: Record<string, string>;
	dpe?: GoldenDpeInvariants;
};

type GoldenResolve = Omit<ResultatMultiResolution, 'dpe_attribution'> & {
	dpe_attribution?: DpeAttributionV2 | null;
};

type GoldenRealMetadata = {
	source_millesimes: Record<string, string>;
	invariants: GoldenInvariants;
	carrier_for?: GoldenCarrier[];
};

type GoldenCommuneCase = Extract<GoldenSyntheticCase, { type: 'commune' }>;
type GoldenAddressCase = Extract<GoldenSyntheticCase, { type: 'address' }>;

export type GoldenRealCase =
	| (GoldenCommuneCase & GoldenRealMetadata)
	| (Omit<GoldenAddressCase, 'resolve'> & GoldenRealMetadata & { resolve: GoldenResolve });

type PublicCase = {
	case_id: string;
	type: 'commune' | 'address';
	departement: '44' | '85';
	code_insee: string;
	category: GoldenCategory;
	frozen_at: string;
	source_millesimes: Record<string, string>;
	invariants: GoldenInvariants;
	carrier_for?: GoldenCarrier[];
	dpe_attribution?: DpeAttributionV2;
};

const FROZEN_AT = '2026-08-21';

function sourceMillesimes(includeRevenus = true): Record<string, string> {
	return {
		recensement: '2022',
		...(includeRevenus ? { revenus: '2021' } : {}),
		fiscalite: '2025',
		loyers: '2025'
	};
}

function addressDb(overrides: Record<string, boolean> = {}): Record<string, boolean> {
	return {
		has_commune_row: true,
		superficie_present: true,
		parcelle_present: true,
		copro_row_present: false,
		dpe_appartements_multiples: false,
		dvf_appartements_present: false,
		dpe_rattachement_ok: true,
		...overrides
	};
}

function addressApi(overrides: Record<string, string> = {}): Record<string, string> {
	return {
		dpe_state: 'value',
		risques_state: 'value',
		urbanisme_state: 'value',
		cadastre_state: 'value',
		dvf_state: 'value',
		commune_state: 'value',
		...overrides
	};
}

function addressInvariants(
	dpe: GoldenDpeInvariants,
	options: {
		db?: Record<string, boolean>;
		api?: Record<string, string>;
	} = {}
): GoldenInvariants {
	return {
		db: addressDb(options.db),
		api: addressApi(options.api),
		mapper: { prix_m2_unit: 'euro_per_m2' },
		dpe
	};
}

function dpeInvariants(
	status: GoldenDpeInvariants['dpe_attribution_status'],
	inventoryPresence: GoldenDpeInvariants['dpe_inventory_presence'],
	overrides: Partial<GoldenDpeInvariants> = {}
): GoldenDpeInvariants {
	return {
		dpe_inventory_presence: inventoryPresence,
		dpe_attribution_status: status,
		dpe_primary_diagnostic_present: false,
		dpe_truncated: false,
		dpe_nearby_diagnostic_present: false,
		dpe_candidate_count: 0,
		dpe_building_count: 0,
		...overrides
	};
}

function unassignedDpe(
	buildingContext: DpeAttributionV2['building_context'] = null
): DpeAttributionV2 {
	return {
		version: 2,
		status: 'unassigned',
		reason: 'dwelling_identity_unproven',
		diagnostic: null,
		candidates: [],
		building_context: buildingContext,
		graph_state: 'not_checked'
	};
}

function absentDpe(reason: 'identity_unavailable' | 'not_reported'): DpeAttributionV2 {
	return {
		version: 2,
		status: 'absent',
		reason,
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: 'not_checked'
	};
}

const publicCases: PublicCase[] = [
	{
		case_id: 'golden-address-001',
		type: 'address',
		departement: '44',
		code_insee: '44001',
		category: 'complete',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(dpeInvariants('unassigned', 'present')),
		carrier_for: ['dpe_unassigned'],
		dpe_attribution: unassignedDpe()
	},
	{
		case_id: 'golden-address-002',
		type: 'address',
		departement: '44',
		code_insee: '44001',
		category: 'no_dpe',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(dpeInvariants('absent', 'absent'), {
			api: { dpe_state: 'not_reported' }
		}),
		carrier_for: ['dpe_absent'],
		dpe_attribution: absentDpe('not_reported')
	},
	{
		case_id: 'golden-address-003',
		type: 'address',
		departement: '44',
		code_insee: '44001',
		category: 'dataset_out_of_coverage',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(
			dpeInvariants('absent', 'absent', { dpe_nearby_diagnostic_present: true }),
			{ api: { dpe_state: 'not_reported', dvf_state: 'not_reported' } }
		),
		carrier_for: ['dpe_nearer_neighbor_negative', 'dpe_absent'],
		dpe_attribution: absentDpe('not_reported')
	},
	{
		case_id: 'golden-address-004',
		type: 'address',
		departement: '44',
		code_insee: '44001',
		category: 'dataset_out_of_coverage',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(dpeInvariants('absent', 'absent'), {
			api: { dpe_state: 'not_reported', dvf_state: 'not_reported' }
		}),
		carrier_for: ['dpe_absent'],
		dpe_attribution: absentDpe('not_reported')
	},
	{
		case_id: 'golden-address-005',
		type: 'address',
		departement: '44',
		code_insee: '44109',
		category: 'no_dpe',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(
			dpeInvariants('absent', 'absent', { dpe_nearby_diagnostic_present: true }),
			{
				db: {
					copro_row_present: true,
					dpe_appartements_multiples: true,
					dvf_appartements_present: true
				},
				api: { dpe_state: 'not_reported' }
			}
		),
		carrier_for: ['dpe_nearer_neighbor_negative', 'dpe_absent'],
		dpe_attribution: absentDpe('not_reported')
	},
	{
		case_id: 'golden-address-007',
		type: 'address',
		departement: '44',
		code_insee: '44184',
		category: 'complete',
		frozen_at: '2026-09-05',
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(dpeInvariants('unassigned', 'present'), {
			db: {
				dpe_appartements_multiples: true,
				dvf_appartements_present: true
			}
		}),
		carrier_for: ['dpe_unassigned'],
		dpe_attribution: unassignedDpe()
	},
	{
		case_id: 'golden-address-009',
		type: 'address',
		departement: '85',
		code_insee: '85001',
		category: 'no_dpe',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(
			dpeInvariants('absent', 'absent', { dpe_nearby_diagnostic_present: true }),
			{ api: { dpe_state: 'not_reported' } }
		),
		carrier_for: ['dpe_nearer_neighbor_negative', 'dpe_absent'],
		dpe_attribution: absentDpe('not_reported')
	},
	{
		case_id: 'golden-commune-001',
		type: 'commune',
		departement: '44',
		code_insee: '44109',
		category: 'complete',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: {
			db: { has_commune_row: true, superficie_present: true },
			api: { dpe_state: 'value', dvf_state: 'value' },
			mapper: { densite_unit: 'density' }
		}
	},
	{
		case_id: 'golden-commune-002',
		type: 'commune',
		departement: '85',
		code_insee: '85047',
		category: 'complete',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: {
			db: { has_commune_row: true, superficie_present: true },
			api: { dpe_state: 'value', dvf_state: 'value' },
			mapper: { densite_unit: 'density' }
		}
	},
	{
		case_id: 'golden-commune-003',
		type: 'commune',
		departement: '85',
		code_insee: '85165',
		category: 'no_dvf',
		frozen_at: '2026-09-25',
		source_millesimes: sourceMillesimes(false),
		invariants: {
			db: { has_commune_row: true, superficie_present: true },
			api: { dpe_state: 'value', dvf_state: 'not_reported' },
			mapper: { densite_unit: 'density' }
		}
	},
	{
		case_id: 'golden-address-011',
		type: 'address',
		departement: '44',
		code_insee: '44001',
		category: 'no_dvf',
		frozen_at: FROZEN_AT,
		source_millesimes: sourceMillesimes(),
		invariants: addressInvariants(dpeInvariants('unassigned', 'present'), {
			api: { dvf_state: 'not_reported' }
		}),
		carrier_for: ['dpe_unassigned'],
		dpe_attribution: unassignedDpe()
	}
];

function fixtureCommune(codeInsee: string, departement: '44' | '85'): CommuneSynthese {
	// This aggregate was already public and certified by the marketing snapshot before Phase 51.
	// Keep it stable while all property-level/DPE presentation values remain synthetic below.
	if (codeInsee === '44001') {
		return {
			code_insee: '44001',
			nom_commune: 'Abbaretz',
			code_departement: '44',
			code_region: '52',
			population: 2052,
			nb_menages: 862,
			nb_logements: 970,
			nb_logements_vacants_recensement: 80,
			densite: 33,
			superficie: 62.13,
			annee_demographie: 2022,
			revenu_median: 19680,
			taux_pauvrete: 13,
			indice_gini: 0.262,
			part_imposee: 40,
			annee_revenus: 2021,
			zone_abc: 'C',
			zone_abc_reclassement: false,
			taux_vacance: 8.92,
			nb_vacants_plus_2ans: 41,
			annee_vacance: 2026,
			taux_tfpb: 34.99,
			taux_th_rs: 29.86,
			taux_teom: null,
			nom_epci: 'CC de Nozay',
			annee_fiscalite: 2025,
			loyer_m2_appartement: 10.32,
			loyer_m2_maison: 8.81,
			annee_loyers: 2025,
			prix_m2_median: 1429,
			nb_mutations_12m: 25,
			dpe_passoires_pct: 19,
			dpe_median: 'D',
			annee_construction_mediane: 1986,
			nb_equipements: 54,
			nb_etablissements_scolaires: 1,
			nb_monuments: 0,
			nb_proprietaires_pm: 115,
			surface_pm_ha: 2079.2,
			fibre_pct: 98
		};
	}
	return {
		code_insee: codeInsee,
		nom_commune: `Commune synthétique ${departement}`,
		code_departement: departement,
		code_region: '52',
		population: 12000,
		nb_menages: 5200,
		nb_logements: 6100,
		nb_logements_vacants_recensement: 480,
		densite: 210.5,
		superficie: 57.02,
		annee_demographie: 2022,
		revenu_median: 21500,
		taux_pauvrete: 11.2,
		indice_gini: 0.28,
		part_imposee: 52,
		annee_revenus: 2021,
		zone_abc: 'B2',
		zone_abc_reclassement: false,
		taux_vacance: 7.4,
		nb_vacants_plus_2ans: 180,
		annee_vacance: 2023,
		taux_tfpb: 40.5,
		taux_th_rs: 17.5,
		taux_teom: 11,
		nom_epci: 'Intercommunalité synthétique',
		annee_fiscalite: 2025,
		loyer_m2_appartement: 9.5,
		loyer_m2_maison: 8.9,
		annee_loyers: 2025,
		prix_m2_median: 2450,
		nb_mutations_12m: 120,
		dpe_passoires_pct: 6.5,
		dpe_median: 'D',
		annee_construction_mediane: 1988,
		nb_equipements: 640,
		nb_etablissements_scolaires: 3,
		nb_monuments: 0,
		nb_proprietaires_pm: 410,
		surface_pm_ha: 320,
		fibre_pct: 78
	};
}

function datasetValue(dataset: string, state: string, entry: PublicCase): unknown {
	if (state !== 'value') return null;
	if (dataset === 'cadastre') {
		return {
			id_parcelle: `${entry.code_insee}-GOLDEN-${entry.case_id.slice(-3)}`,
			section: null,
			numero: null,
			contenance: null
		};
	}
	if (dataset === 'dpe') {
		return null;
	}
	if (dataset === 'dvf') return [{ prix_m2: 0 }];
	if (dataset === 'risques') return { argiles: null, risques_naturels: [] };
	if (dataset === 'urbanisme') {
		return {
			zones_plu: [{ properties: { libelle: 'GOLDEN', typezone: 'U' } }],
			prescriptions: []
		};
	}
	return null;
}

function syntheticResolve(entry: PublicCase): GoldenResolve {
	const api = entry.invariants.api;
	const datasets: Record<string, unknown> = {};
	for (const dataset of ['dpe', 'risques', 'urbanisme', 'cadastre', 'dvf']) {
		const state = api[`${dataset}_state`];
		if (state === 'out_of_coverage' || state === 'not_applicable') continue;
		datasets[dataset] = datasetValue(dataset, state, entry);
	}
	return {
		type_detecte: 'adresse',
		latitude: 0,
		longitude: 0,
		adresse: null,
		id_parcelle: null,
		commune: null,
		code_postal: null,
		score: null,
		code_insee: entry.code_insee,
		dpe_attribution: entry.dpe_attribution ?? null,
		datasets,
		erreurs: [],
		avertissements: []
	};
}

function buildCase(entry: PublicCase): GoldenRealCase {
	const commune = fixtureCommune(entry.code_insee, entry.departement);
	const { dpe_attribution: _dpeAttribution, ...publicEntry } = entry;
	if (entry.type === 'commune') {
		// A commune case frozen without DVF (golden-commune-003, re-frozen on 2026-09-25:
		// 85165 has no sale in the DVF window) carries no DVF aggregate.
		const noDvf = entry.invariants.api.dvf_state !== 'value';
		return {
			...publicEntry,
			commune: noDvf ? { ...commune, prix_m2_median: null, nb_mutations_12m: null } : commune
		} as GoldenRealCase;
	}
	return {
		...publicEntry,
		commune,
		resolve: syntheticResolve(entry)
	} as GoldenRealCase;
}

// Public identifiers and invariants mirror the approved manifest. Every presentation value above
// is synthetic; no private replay input, precise coordinate or neighbouring diagnostic is kept.
export const GOLDEN_REAL_CORPUS: GoldenRealCase[] = publicCases.map(buildCase);
