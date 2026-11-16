import { readFileSync } from 'node:fs';
import { resolve as joinPath } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the low-level client so resolveBien exercises no network.
vi.mock('./client', async () => {
	const actual = await vi.importActual<typeof import('./client')>('./client');
	return { ...actual, api: vi.fn() };
});

import { api } from './client';
import {
	resolveBien,
	resolveBienParAdresse,
	resolveBienProxies,
	type ResultatMultiResolution,
	type DatasetErreur,
	type Avertissement,
	type DatasetProvenance,
	type MetaResolve,
	type ReponseResolve
} from './resolve';
import { getLogementsParParcelle, type ReponseLogements } from './logements';

const mockApi = api as ReturnType<typeof vi.fn>;

/** Build a minimal /v1/resolve envelope holding a single ResultatMultiResolution. */
function envelope(
	multi: Partial<ResultatMultiResolution> = {},
	meta: MetaResolve = {}
): ReponseResolve {
	const donnee: ResultatMultiResolution = {
		type_detecte: 'gps',
		latitude: 46.83,
		longitude: -1.9,
		adresse: null,
		id_parcelle: null,
		commune: null,
		code_postal: null,
		score: null,
		datasets: {},
		erreurs: [],
		avertissements: [],
		...multi
	};
	return { statut: 'ok', donnees: [donnee], meta };
}

beforeEach(() => {
	mockApi.mockReset();
});

describe('resolveBien()', () => {
	it('builds the /v1/resolve query with q=lat,lon, type=gps and datasets including commune (Pitfall 4)', async () => {
		mockApi.mockResolvedValue(envelope());
		await resolveBien(46.83, -1.9);
		const url = mockApi.mock.calls[0][0] as string;
		expect(url).toContain('/v1/resolve');
		// URLSearchParams encodes the comma in q as %2C.
		expect(url).toContain('q=46.83%2C-1.9');
		expect(url).toContain('type=gps');
		// commune MUST be in the requested datasets — single round-trip (Pitfall 4).
		expect(url).toContain('datasets=cadastre%2Cdpe%2Cdvf%2Ccommune');
		expect(mockApi.mock.calls[0][1]).toEqual({ cache: 'no-store' });
	});

	it('does NOT request the two live proxies on the fast leg (Phase 50 — 11,6 s measured)', async () => {
		// The invariant this locks is a LATENCY one: `risques` (Géorisques) and `urbanisme`
		// (GPU/IGN) are live upstream proxies bounded only by the backend's 30 s httpx timeout.
		// Putting either back in this call re-couples the whole SSR render to the slowest
		// upstream — which is the defect Phase 50 removed, not a stylistic preference.
		mockApi.mockResolvedValue(envelope());
		await resolveBien(46.83, -1.9);
		const url = mockApi.mock.calls[0][0] as string;
		expect(url).not.toContain('risques');
		expect(url).not.toContain('urbanisme');
	});

	it('returns donnees[0] of the envelope (donnees is a LIST with 1 element)', async () => {
		mockApi.mockResolvedValue(envelope({ commune: 'Challans', code_postal: '85300' }));
		const res = await resolveBien(46.836902, -1.902042);
		expect(res?.commune).toBe('Challans');
		expect(res?.code_postal).toBe('85300');
		expect(res?.type_detecte).toBe('gps');
	});

	it('returns null on an empty envelope (200 { donnees: [] }, out-of-perimeter coords) — CR-01', async () => {
		// The backend returns 200 with donnees:[] for coordinates outside the loaded perimeter.
		// resolveBien MUST surface this as null so the page routes to the empty-state screen,
		// never undefined (which would TypeError on multi.datasets → wrong "error" screen).
		mockApi.mockResolvedValue({ statut: 'ok', donnees: [], meta: {} });
		const res = await resolveBien(46.83, -1.9);
		expect(res).toBeNull();
	});

	it('exports the DatasetErreur / Avertissement / ResultatMultiResolution types (compile-time assertion)', () => {
		// If these type imports did not resolve, the test file would not compile.
		const err: DatasetErreur = { dataset: 'errial', message: 'boom' };
		const warn: Avertissement = {
			dataset: 'connectivite',
			code: 'ADRESSE_INCOMPLETE',
			message: '...'
		};
		const multi: ResultatMultiResolution = {
			type_detecte: 'gps',
			latitude: 0,
			longitude: 0,
			adresse: null,
			id_parcelle: null,
			commune: null,
			code_postal: null,
			score: null,
			datasets: null,
			erreurs: [err],
			avertissements: [warn]
		};
		expect(multi.erreurs?.[0].dataset).toBe('errial');
		expect(multi.avertissements[0].code).toBe('ADRESSE_INCOMPLETE');
	});

	it('transmits fetchImpl as the 3rd positional argument to api() (SSR-safe, D-04)', async () => {
		mockApi.mockResolvedValue(envelope());
		const fetchSpy = vi.fn() as unknown as typeof fetch;
		await resolveBien(46.83, -1.9, fetchSpy);
		expect(mockApi.mock.calls[0][2]).toBe(fetchSpy);
	});

	it('stays backward-compatible without fetchImpl (undefined passed through, api() defaults to global fetch)', async () => {
		mockApi.mockResolvedValue(envelope());
		const res = await resolveBien(46.83, -1.9);
		expect(mockApi.mock.calls[0][2]).toBeUndefined();
		// Envelope behavior (empty donnees → null) stays unaffected by the new param.
		expect(res).not.toBeNull();
	});
});

describe('resolveBienParAdresse()', () => {
	it('builds the /v1/resolve query with type=adresse, q=<texte> and datasets=DATASETS_BIEN, transmitting fetchImpl', async () => {
		mockApi.mockResolvedValue(envelope());
		const fetchSpy = vi.fn() as unknown as typeof fetch;
		await resolveBienParAdresse('12 Rue de la Paix, 44000 Nantes', fetchSpy);
		const url = mockApi.mock.calls[0][0] as string;
		expect(url).toContain('/v1/resolve');
		expect(url).toContain('type=adresse');
		// URLSearchParams encodes space as "+" and comma as %2C.
		expect(url).toContain('q=12+Rue+de+la+Paix%2C+44000+Nantes');
		expect(url).toContain('datasets=cadastre%2Cdpe%2Cdvf%2Ccommune');
		expect(mockApi.mock.calls[0][1]).toEqual({ cache: 'no-store' });
		expect(mockApi.mock.calls[0][2]).toBe(fetchSpy);
	});

	it('returns the full ReponseResolve envelope (loader needs meta.resolution, not donnees[0])', async () => {
		const env = envelope({ commune: 'Nantes' });
		mockApi.mockResolvedValue(env);
		const res = await resolveBienParAdresse('12 Rue de la Paix, 44000 Nantes');
		expect(res).toBe(env);
		expect(res.donnees[0].commune).toBe('Nantes');
	});
});

describe('resolveBienProxies() — slow leg (Phase 50)', () => {
	/** Minimal but realistic DatasetProvenance for the GPU/IGN urbanisme proxy. */
	const provenanceUrbanisme: DatasetProvenance = {
		nom: 'Géoportail de l’urbanisme (GPU)',
		licence: 'Licence ouverte',
		url: 'https://www.geoportail-urbanisme.gouv.fr/',
		producteur: 'IGN',
		dataset: 'urbanisme',
		periode: {
			statut: 'glissante',
			label: 'en continu',
			debut: null,
			fin: null
		},
		derniere_importation_reussie: null,
		couverture: { type: 'nationale', departements: null },
		cadence: {
			statut: 'non_publiee_ou_irreguliere',
			label: null,
			evidence_url: null,
			intervalle_jours: null
		},
		etat: 'actuel'
	};

	it('requests ONLY risques + urbanisme, by GPS, and transmits fetchImpl (SSR-safe)', async () => {
		mockApi.mockResolvedValue(envelope());
		const fetchSpy = vi.fn() as unknown as typeof fetch;
		await resolveBienProxies(46.83, -1.9, fetchSpy);
		const url = mockApi.mock.calls[0][0] as string;
		expect(url).toContain('/v1/resolve');
		expect(url).toContain('q=46.83%2C-1.9');
		expect(url).toContain('type=gps');
		expect(url).toContain('datasets=risques%2Curbanisme');
		// The fast leg's datasets must NOT be re-requested here — the second call would then be
		// as slow as the first was, and would re-resolve data the page already holds.
		expect(url).not.toContain('cadastre');
		expect(url).not.toContain('commune');
		expect(mockApi.mock.calls[0][2]).toBe(fetchSpy);
	});

	it('returns the mergeable part of donnees[0] plus meta.source as `provenance`', async () => {
		const env = envelope(
			{
				datasets: {
					risques: { risques_naturels: [] },
					urbanisme: { zones_plu: [] }
				},
				erreurs: [{ dataset: 'urbanisme', message: 'timeout' }],
				avertissements: [{ dataset: 'risques', code: 'ADRESSE_INCOMPLETE', message: '...' }],
				dataset_states: {
					risques: {
						dataset: 'risques',
						presence: 'value',
						absence_reason: null,
						raison: null
					}
				}
			},
			{ source: [provenanceUrbanisme] }
		);
		mockApi.mockResolvedValue(env);

		const res = await resolveBienProxies(46.83, -1.9);
		expect(res).toEqual({
			datasets: {
				risques: { risques_naturels: [] },
				urbanisme: { zones_plu: [] }
			},
			erreurs: [{ dataset: 'urbanisme', message: 'timeout' }],
			avertissements: [{ dataset: 'risques', code: 'ADRESSE_INCOMPLETE', message: '...' }],
			dataset_states: {
				risques: {
					dataset: 'risques',
					presence: 'value',
					absence_reason: null,
					raison: null
				}
			},
			provenance: [provenanceUrbanisme]
		});
		// Identity fields are deliberately NOT carried: the fast leg already resolved them, and a
		// second copy could only diverge from the address the page is rendering.
		expect(res).not.toHaveProperty('adresse');
		expect(res).not.toHaveProperty('id_parcelle');
	});

	it('defaults provenance/dataset_states to null when meta carries nothing', async () => {
		mockApi.mockResolvedValue(envelope());
		const res = await resolveBienProxies(46.83, -1.9);
		expect(res?.provenance).toBeNull();
		expect(res?.dataset_states).toBeNull();
		expect(res?.avertissements).toEqual([]);
	});

	it('returns null on an empty envelope (nothing to merge) — same contract as resolveBien', async () => {
		mockApi.mockResolvedValue({ statut: 'ok', donnees: [], meta: {} });
		expect(await resolveBienProxies(46.83, -1.9)).toBeNull();
	});
});

// --- Phase 58.1: closed DPE inventory and attribution V2 -------------------------------

const SRC_RESOLVE = joinPath(process.cwd(), 'src/lib/api/resolve.ts');
const SRC_LOGEMENTS = joinPath(process.cwd(), 'src/lib/api/logements.ts');

function inventoryPayload(patch: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		version: 2,
		state: 'ready',
		context_ref: 'ctx-opaque',
		items: [
			{
				document_ref: 'doc-opaque',
				type_batiment: 'appartement',
				surface_habitable: 52,
				etiquette_dpe: 'D',
				etiquette_ges: 'C',
				date_etablissement_dpe: '2025-01-01',
				date_fin_validite_dpe: null,
				numero_dpe: 'ADEME-FORBIDDEN'
			}
		],
		total: 1,
		distribution: { A: 0, B: 0, C: 0, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
		page_size: 20,
		has_more: false,
		next_cursor: null,
		read_state: 'complete',
		association: 'source_ban_equality',
		address_check: 'unverified',
		source: 'ADEME',
		source_coverage: 'unknown',
		graph_state: 'not_checked',
		imported_at: null,
		ban_id: 'BAN-FORBIDDEN',
		...patch
	};
}

function attributionV2(patch: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		version: 2,
		status: 'unassigned',
		reason: 'dwelling_identity_unproven',
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: 'not_checked',
		...patch
	};
}

describe('DPE V2 projections inside /v1/resolve', () => {
	it('projects inventory and unassigned attribution field by field', async () => {
		const env = envelope({
			dpe_inventory: inventoryPayload() as never,
			dpe_attribution: attributionV2({
				internal_selection: 'FORBIDDEN',
				building_context: {
					count: 2,
					distribution: { C: 1, D: 1 },
					truncated: false
				}
			}) as never
		});
		mockApi.mockResolvedValue(env);

		const result = await resolveBien(46.83, -1.9);
		expect(result).toBe(env.donnees[0]);
		expect(result?.dpe_attribution).toEqual({
			version: 2,
			status: 'unassigned',
			reason: 'dwelling_identity_unproven',
			diagnostic: null,
			candidates: [],
			building_context: {
				count: 2,
				distribution: { C: 1, D: 1 },
				truncated: false
			},
			graph_state: 'not_checked'
		});
		expect(result?.dpe_inventory?.as_of_date).toBeNull();
		expect(result?.dpe_inventory?.items[0]).toEqual({
			document_ref: 'doc-opaque',
			type_batiment: 'appartement',
			surface_habitable: 52,
			etiquette_dpe: 'D',
			etiquette_ges: 'C',
			date_etablissement_dpe: '2025-01-01',
			date_fin_validite_dpe: null,
			lifecycle: null
		});
		expect(JSON.stringify(result?.dpe_inventory)).not.toMatch(/FORBIDDEN/);
		expect(JSON.stringify(result?.dpe_attribution)).not.toMatch(/FORBIDDEN/);
	});


 it('preserves the complete dated lifecycle through Resolve without source selectors', async () => {
  const lifecycle = {
   source_state: 'present', source_snapshot_date: '2026-09-08', source_scope: '44/85',
   regimes: ['existant', 'neuf'], replacement_state: 'replaced', graph_issues: ['fork'],
   validity: 'unknown', as_of_date: '2026-09-08', revision_status: 'unverified',
   revision_kind: null, revision_date: null, detail_availability: 'available'
  };
  const inventory = inventoryPayload();
  const sourceItem = (inventory.items as Record<string, unknown>[])[0];
  mockApi.mockResolvedValue(envelope({
   dpe_inventory: inventoryPayload({as_of_date: '2026-09-08', items: [{...sourceItem, lifecycle}]}) as never
  }));
  const result = await resolveBien(46.83, -1.9);
  expect(result?.dpe_inventory?.as_of_date).toBe('2026-09-08');
  expect(result?.dpe_inventory?.items[0]).toEqual({
   document_ref: 'doc-opaque', type_batiment: 'appartement', surface_habitable: 52,
   etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement_dpe: '2025-01-01',
   date_fin_validite_dpe: null, lifecycle
  });
  expect(JSON.stringify(result?.dpe_inventory)).not.toMatch(/FORBIDDEN|numero_dpe|ban_id/);
 });

	it('refuses a non-versioned legacy exact without erasing a valid inventory', async () => {
		const env = envelope({
			datasets: {
				dpe: [{ numero_dpe: 'ADEME-FORBIDDEN' }],
				dvf: { legitimate: 'kept' }
			},
			dpe_inventory: inventoryPayload() as never,
			dpe_attribution: {
				status: 'exact',
				diagnostic: { numero_dpe: 'ADEME-FORBIDDEN', etiquette_dpe: 'A' },
				candidates: []
			} as never
		});
		mockApi.mockResolvedValue(env);

		const result = await resolveBien(46.83, -1.9);
		expect(result?.dpe_attribution).toEqual({
			version: 2,
			status: 'unavailable',
			reason: 'contract_unavailable',
			diagnostic: null,
			candidates: [],
			building_context: null,
			graph_state: 'not_checked'
		});
		expect(result?.dpe_inventory?.state).toBe('ready');
		expect(result?.dpe_inventory?.items).toHaveLength(1);
		expect(result?.datasets?.dpe).toBeNull();
		expect(result?.datasets?.dvf).toEqual({ legitimate: 'kept' });
		expect(JSON.stringify(result)).not.toContain('ADEME-FORBIDDEN');
	});

	it('refuses an unknown attribution version in-place in the full address envelope', async () => {
		const env = envelope({
			dpe_inventory: inventoryPayload() as never,
			dpe_attribution: attributionV2({ version: 99 }) as never
		});
		mockApi.mockResolvedValue(env);

		const result = await resolveBienParAdresse('12 rue de la Paix');
		expect(result).toBe(env);
		expect(result.donnees[0].dpe_attribution?.status).toBe('unavailable');
		expect(result.donnees[0].dpe_inventory?.context_ref).toBe('ctx-opaque');
	});

	it('drops a malformed embedded inventory without erasing valid DVF or cadastre data', async () => {
		const env = envelope({
			datasets: {
				cadastre: { parcelle: 'parcel-legitimate' },
				dvf: { mutation: 'sale-legitimate' }
			},
			dpe_inventory: inventoryPayload({ version: 99 }) as never,
			dpe_attribution: attributionV2() as never
		});
		mockApi.mockResolvedValue(env);

		const result = await resolveBien(46.83, -1.9);

		expect(result?.dpe_inventory).toBeNull();
		expect(result?.dpe_attribution?.status).toBe('unassigned');
		expect(result?.datasets?.cadastre).toEqual({ parcelle: 'parcel-legitimate' });
		expect(result?.datasets?.dvf).toEqual({ mutation: 'sale-legitimate' });
	});

	it('preserves the distinct V2 absence and outage reasons', async () => {
		for (const [status, reason] of [
			['absent', 'identity_unavailable'],
			['absent', 'not_reported'],
			['error', 'temporary_error']
		] as const) {
			const env = envelope({
				dpe_attribution: attributionV2({ status, reason }) as never
			});
			mockApi.mockResolvedValueOnce(env);
			const result = await resolveBien(46.83, -1.9);
			expect(result?.dpe_attribution).toMatchObject({
				version: 2,
				status,
				reason
			});
		}
	});

	it('neutralizes legacy logement identities while preserving DVF and the shared V2 contracts', async () => {
		const logementsEnvelope: ReponseLogements = {
			statut: 'ok',
			donnees: {
				id_parcelle: 'parcel-legitimate',
				detection: {
					signaux: {},
					signaux_positifs: 1,
					mode: 'immeuble'
				},
				denominateur: {
					logements_connus: 1,
					lots_declares: 2,
					non_distinguables: 1
				},
				distribution_dpe: { D: 1, 'ADEME-FORBIDDEN': 1 },
				ventes_immeuble: [
					{
						date_mutation: '2025-01-10',
						type_local: 'Appartement',
						surface_reelle_bati: 52,
						nombre_pieces_principales: 3,
						valeur_fonciere: 210000,
						classe_mutation: 'Vente mono-locale',
						mono_local: true,
						hors_stats_vefa: false,
						prix_m2: 4038,
						prix_m2_raison: null,
						nb_locaux_residentiel: 1,
						nb_locaux_commercial: 0
					}
				],
				diagnostics_charges: 1,
				tronque: false,
				dpe_attribution: attributionV2() as never,
				dpe_inventory: inventoryPayload() as never
			},
			meta: { source: { dvf: 'legitimate' } }
		};
		mockApi.mockResolvedValue(logementsEnvelope);

		const result = await getLogementsParParcelle(
			'parcel-legitimate',
			'12 rue des Tests',
			'ban-legitimate'
		);

		expect(result).toBe(logementsEnvelope);
		expect(mockApi.mock.calls[0][1]).toEqual({ cache: 'no-store' });
		// Phase 01-04: the always-empty `logements` list was removed from the contract, so the
		// served payload carries no card key at all. A rogue payload that still did is ignored
		// by the view — proven in logements-view.test.ts, not here.
		expect('logements' in result.donnees).toBe(false);
		expect(result.donnees.distribution_dpe).toEqual({});
		expect(result.donnees.dpe_inventory?.items[0].document_ref).toBe('doc-opaque');
		expect(result.donnees.dpe_attribution.status).toBe('unassigned');
		expect(result.donnees.ventes_immeuble[0]).toMatchObject({
			date_mutation: '2025-01-10',
			surface_reelle_bati: 52,
			nombre_pieces_principales: 3
		});
		expect(result.meta).toEqual({ source: { dvf: 'legitimate' } });
		expect(JSON.stringify(result)).not.toContain('ADEME-FORBIDDEN');
	});
});

describe('DPE V2 wiring guards', () => {
	it('uses the shared closed projectors on both resolve entry points', () => {
		const source = readFileSync(SRC_RESOLVE, 'utf8');
		expect(source).toContain('projectEmbeddedDpeInventory');
		expect(source).toContain('projectDpeAttributionForClient');
		expect(source.match(/projectDpeContracts\(/g)?.length).toBeGreaterThanOrEqual(3);
	});

	it('keeps the logements client on the same shared projector instead of defining a second rule', () => {
		const source = readFileSync(SRC_LOGEMENTS, 'utf8');
		expect(source).toContain("from './dpe-inventory'");
		expect(source).toContain('projectEmbeddedDpeInventory');
		expect(source).toContain('projectDpeAttributionForClient');
		expect(source).not.toMatch(/function\s+projectDpeAttribution/);
	});
});
