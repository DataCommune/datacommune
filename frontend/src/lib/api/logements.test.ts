import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the low-level client so getLogementsParParcelle exercises no network.
vi.mock('./client', async () => {
	const actual = await vi.importActual<typeof import('./client')>('./client');
	return { ...actual, api: vi.fn() };
});

import { api, ApiError } from './client';
import {
	getLogementsParParcelle,
	type ReponseLogements,
	type ResultatLogements,
	type VenteImmeuble
} from './logements';

const mockApi = api as ReturnType<typeof vi.fn>;

const ID_PARCELLE = '85047000AB0042';

/** Build a minimal /v1/bien/logements envelope (donnees is an OBJECT, not a list). */
function envelope(resultat: Partial<ResultatLogements> = {}): ReponseLogements {
	const donnees: ResultatLogements = {
		id_parcelle: ID_PARCELLE,
		detection: {
			signaux: {
				copro_rnc: { positif: true, presence: 'value', absence_reason: null, compte: 1 },
				dpe_appart: { positif: true, presence: 'value', absence_reason: null, compte: 5 },
				dvf_appart: { positif: false, presence: 'absent', absence_reason: 'not_reported', compte: 0 }
			},
			signaux_positifs: 2,
			mode: 'immeuble'
		},
		denominateur: { logements_connus: 2, lots_declares: 12, non_distinguables: 3 },
		// Phase 51: `logements` and `distribution_dpe` are PROJECTIONS of the decision below and
		// cannot contradict it (backend validator `_valide_decision_dpe`). This fixture is the
		// `building` state: an aggregate, zero card — the combination that used to be published
		// as clickable neighbours.
		distribution_dpe: { C: 1, D: 3, E: 1 },
		ventes_immeuble: [],
		diagnostics_charges: 5,
		tronque: false,
		dpe_attribution: {
			status: 'building',
			reason: 'not_reported',
			diagnostic: null,
			candidates: [],
			building_context: { count: 5, distribution: { C: 1, D: 3, E: 1 }, truncated: false }
		},
		...resultat
	};
	return { statut: 'ok', donnees, meta: { source: [] } };
}

beforeEach(() => {
	mockApi.mockReset();
});

describe('getLogementsParParcelle()', () => {
	it('targets the dedicated /v1/bien/logements endpoint with id_parcelle, never /v1/resolve (D-12)', async () => {
		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE);
		const url = mockApi.mock.calls[0][0] as string;
		expect(url).toContain('/v1/bien/logements');
		expect(url).toContain(`id_parcelle=${ID_PARCELLE}`);
		// D-12: the certified /v1/resolve contract is never extended nor reused here.
		expect(url).not.toContain('/v1/resolve');
	});

	it('transmits fetchImpl as the last positional argument to api() (SSR-safe, Pitfall 4)', async () => {
		mockApi.mockResolvedValue(envelope());
		const fetchSpy = vi.fn() as unknown as typeof fetch;
		await getLogementsParParcelle(ID_PARCELLE, undefined, undefined, fetchSpy);
		expect(mockApi.mock.calls[0][2]).toBe(fetchSpy);
	});

	it('forwards ban_id verbatim when the resolution carries one (RESEARCH-L1)', async () => {
		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE, undefined, '44109_zz01_00002');
		// La clé BAN est la voie de rattachement EXACTE des DPE côté backend : sans elle, le
		// rattachement retombe sur la géométrie et rate les points BAN posés sur la voirie.
		expect(mockApi.mock.calls[0][0] as string).toContain('ban_id=44109_zz01_00002');
	});

	it('omits ban_id from the query when the resolution carries none (or blank)', async () => {
		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE);
		expect(mockApi.mock.calls[0][0] as string).not.toContain('ban_id=');

		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE, undefined, '  ');
		expect(mockApi.mock.calls[1][0] as string).not.toContain('ban_id=');
	});

	it('omits adresse from the query when it is not provided (or empty)', async () => {
		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE);
		expect(mockApi.mock.calls[0][0] as string).not.toContain('adresse=');

		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE, '   ');
		expect(mockApi.mock.calls[1][0] as string).not.toContain('adresse=');
	});

	it('encodes adresse through URLSearchParams when provided (never raw concatenation)', async () => {
		mockApi.mockResolvedValue(envelope());
		await getLogementsParParcelle(ID_PARCELLE, '12 Rue de la Paix, Challans');
		const url = mockApi.mock.calls[0][0] as string;
		// URLSearchParams encodes space as "+" and comma as %2C.
		expect(url).toContain('adresse=12+Rue+de+la+Paix%2C+Challans');
		expect(url).not.toContain('adresse=12 Rue');
	});

	it('propagates the ApiError raised by api() (S7 degradation is decided in the loader)', async () => {
		const boom = new ApiError(503, 'service indisponible');
		mockApi.mockRejectedValue(boom);
		await expect(getLogementsParParcelle(ID_PARCELLE)).rejects.toBe(boom);
	});

	it('returns the FULL envelope (statut/donnees/meta), not donnees alone', async () => {
		const env = envelope({ diagnostics_charges: 42 });
		mockApi.mockResolvedValue(env);
		const res = await getLogementsParParcelle(ID_PARCELLE);
		expect(res).toBe(env);
		expect(res.statut).toBe('ok');
		expect(res.meta).toBeDefined();
		expect(res.donnees.diagnostics_charges).toBe(42);
		expect(res.donnees.detection.mode).toBe('immeuble');
	});

	it('exports VenteImmeuble mirroring the mutation-grain contract (D-17, compile-time)', () => {
		// Field-for-field mirror of app/logements/schemas.py::VenteImmeuble. If a name or a
		// nullability drifted on either side, this literal would stop compiling.
		const vente: VenteImmeuble = {
			date_mutation: '2024-05-14',
			type_local: null,
			surface_reelle_bati: 310,
			nombre_pieces_principales: 14,
			valeur_fonciere: 980000,
			classe_mutation: 'ensemble_residentiel',
			mono_local: false,
			hors_stats_vefa: false,
			prix_m2: null,
			prix_m2_raison: 'mutation_multi_locaux',
			nb_locaux_residentiel: 5,
			nb_locaux_commercial: 0
		};
		// D-17: an amount never travels without the label saying what was sold — the backend
		// makes the unlabelled payload unconstructible, this pins the mirror to it.
		expect(vente.valeur_fonciere).not.toBeNull();
		expect(vente.classe_mutation).toBe('ensemble_residentiel');
		// A vente d'ensemble is a GLOBAL price: no honest price per m2 exists on it, and the
		// reason for that absence is published rather than left blank.
		expect(vente.mono_local).toBe(false);
		expect(vente.prix_m2).toBeNull();
		expect(vente.prix_m2_raison).toBe('mutation_multi_locaux');
	});
});
