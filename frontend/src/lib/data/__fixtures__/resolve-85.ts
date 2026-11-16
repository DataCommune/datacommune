import type { ResultatMultiResolution } from '$lib/api/resolve';
import { COMMUNE_85047_CHALLANS } from './commune-85';

/**
 * Frozen representative /v1/resolve envelope element for a property in Challans (85047).
 *
 * DEGRADED MODE (2026-06-03): api.datacommune.fr returned HTTP 530 when this fixture was
 * frozen, so the live payload could NOT be confirmed. Values are reconstructed from the
 * resolve/schemas.py contract + the RESEARCH §Code Examples shape ("à confirmer quand l'API
 * sera up"). Chosen to exercise the THREE per-dataset states:
 *   - cadastre/dpe/dvf/urbanisme/commune → 'ok' (real payloads, heterogeneous shapes Pitfall 2)
 *   - errial → 'error' (present in `erreurs[]`, absent from `datasets{}` — Pitfall 1)
 *   - any dataset not requested (e.g. monuments) → 'absent' (key missing from `datasets`)
 *
 * `commune` (the NAME) is distinct from the INSEE code, which lives in the `commune`
 * dataset payload (COMMUNE_85047_CHALLANS.code_insee — Pitfall 4).
 */
export const mockResolve85: ResultatMultiResolution = {
	type_detecte: 'gps',
	latitude: 46.836902,
	longitude: -1.902042,
	adresse: '8 Impasse des rives du port, 85300 Challans',
	id_parcelle: '85047000AB0042',
	commune: 'Challans', // NAME, not the INSEE code (Pitfall 4)
	code_postal: '85300',
	score: null,
	datasets: {
		cadastre: {
			id_parcelle: '85047000AB0042',
			commune: '85047',
			prefixe: '000',
			section: 'AB',
			numero: '0042',
			contenance: 520,
			centroide_lat: 46.836902,
			centroide_lon: -1.902042
		},
		// dpe is a LIST (≤3 nearby rows) — Pitfall 2.
		dpe: [
			{
				numero_dpe: '2285E0123456',
				etiquette_dpe: 'F',
				etiquette_ges: 'E',
				surface_habitable: 78,
				annee_construction: 1975,
				periode_construction: '1975-1977',
				type_batiment: 'appartement',
				conso_5_usages_ep: 412,
				emission_ges_5_usages: 48,
				cout_total_5_usages: 1180,
				type_energie_principale_chauffage: 'Électricité',
				qualite_isolation_enveloppe: 'moyenne',
				type_installation_chauffage: 'individuel électrique'
			}
		],
		// dvf is a LIST (≤5 recent sales) — Pitfall 2.
		dvf: [
			{
				id_mutation: '2023-123456',
				date_mutation: '2023-09-12',
				valeur_fonciere: 168000,
				type_local: 'Appartement',
				surface_reelle_bati: 78,
				nombre_pieces_principales: 3,
				prix_m2: 2154,
				adresse_numero: '8',
				adresse_nom_voie: 'Impasse des rives du port',
				id_parcelle: '85047000AB0042'
			}
		],
		// risques: real Georisques aggregate shape (WR-05, confirmed live 2026-06-04).
		// Natural risks live in risques_naturels[].risques_detail[].libelle_risque_long (commune-level);
		// argiles is a {codeExposition, exposition} dict; radon/icpe may be null/empty per coords.
		risques: {
			argiles: { codeExposition: '2', exposition: 'Exposition moyenne' },
			icpe: [],
			radon: null,
			risques_naturels: [
				{
					code_insee: '85047',
					libelle_commune: 'CHALLANS',
					risques_detail: [
						{ num_risque: '11', libelle_risque_long: 'Inondation', zone_sismicite: null },
						{ num_risque: '13', libelle_risque_long: 'Séisme', zone_sismicite: null },
						{ num_risque: '18', libelle_risque_long: 'Radon', zone_sismicite: null }
					]
				}
			]
		},
		// urbanisme is an object {zones_plu:[...], prescriptions:[]} — Pitfall 2.
		urbanisme: {
			zones_plu: [{ properties: { libelle: 'Ua', typezone: 'U' } }],
			prescriptions: []
		},
		// commune is a single CommuneSynthese object (28 fields, incl. code_insee — Pitfall 4).
		commune: COMMUNE_85047_CHALLANS
	},
	// errial was requested but crashed → 'error' state (LIST, not a dict — Pitfall 1).
	erreurs: [{ dataset: 'errial', message: "Erreur lors de l'interrogation du dataset errial" }],
	avertissements: []
};

/** All-empty envelope: every requested dataset returned nothing — pure degraded path. */
export const mockResolveAllEmpty: ResultatMultiResolution = {
	type_detecte: 'gps',
	latitude: 46.836902,
	longitude: -1.902042,
	adresse: null,
	id_parcelle: null,
	commune: null,
	code_postal: null,
	score: null,
	datasets: {},
	erreurs: [],
	avertissements: []
};

/** Risques dataset in error → its theme degrades while other themes still render (D-04). */
export const mockResolveErrorRisques: ResultatMultiResolution = {
	...mockResolve85,
	datasets: {
		// risques omitted from datasets — it lives in erreurs[] instead.
		cadastre: mockResolve85.datasets!.cadastre,
		dpe: mockResolve85.datasets!.dpe,
		dvf: mockResolve85.datasets!.dvf,
		urbanisme: mockResolve85.datasets!.urbanisme,
		commune: mockResolve85.datasets!.commune
	},
	erreurs: [{ dataset: 'risques', message: 'Erreur georisques' }]
};

/** A dataset carrying a non-blocking ADRESSE_INCOMPLETE warning → commune-level, not bien (Pitfall 3). */
export const mockResolveWithAvertissement: ResultatMultiResolution = {
	...mockResolve85,
	avertissements: [
		{
			dataset: 'connectivite',
			code: 'ADRESSE_INCOMPLETE',
			message: 'Adresse incomplète : résultats au niveau communal'
		}
	]
};
