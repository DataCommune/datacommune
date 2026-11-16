import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve as resolvePath } from 'node:path';

vi.mock('./client', async (importOriginal) => {
	const actual = await importOriginal<typeof import('./client')>();
	return { ...actual, api: vi.fn() };
});

import { ApiError, api } from './client';
import {
	DPE_DOCUMENT_ROUTE,
	DPE_DOCUMENT_UNITS,
	DPE_INVENTORY_DEFAULT_PAGE_SIZE,
	DPE_INVENTORY_ERROR_STATUS,
	DPE_INVENTORY_MAX_PAGE_SIZE,
	DPE_INVENTORY_ROUTE,
	DpeInventoryContractError,
	DpeInventoryRequestError,
	getDpeDocument,
	getDpeInventory,
	projectDpeAttributionForClient,
	projectDpeAttributionV2,
	projectDpeConsultedDocument,
	projectDpeInventoryPage
} from './dpe-inventory';

const apiMock = vi.mocked(api);

const distribution = {
	A: 0,
	B: 0,
	C: 0,
	D: 1,
	E: 0,
	F: 0,
	G: 0,
	unknown: 0
};

function inventoryPayload(patch: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		version: 2,
		state: 'ready',
		context_ref: 'ctx-opaque',
		items: [
			{
				document_ref: 'doc-opaque',
				type_batiment: 'appartement',
				surface_habitable: null,
				etiquette_dpe: 'D',
				etiquette_ges: null,
				date_etablissement_dpe: '2025-03-15',
				date_fin_validite_dpe: null,
				numero_dpe: 'ADEME-FORBIDDEN',
				adresse_brute: 'ADDRESS-FORBIDDEN',
				latitude: 47.1,
				etage: 3,
				porte: 'gauche'
			}
		],
		total: 1,
		distribution: { ...distribution, injected: 4 },
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
		identifiant_ban: 'BAN-FORBIDDEN',
		kpi: 'FORBIDDEN',
		selection: 'FORBIDDEN',
		...patch
	};
}

function documentPayload(patch: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		version: 2,
		context_ref: 'ctx-opaque',
		document_ref: 'doc-opaque',
		type_batiment: 'appartement',
		surface_habitable: 61.5,
		etiquette_dpe: 'D',
		etiquette_ges: 'B',
		date_etablissement_dpe: '2025-03-15',
		date_fin_validite_dpe: null,
		annee_construction: 1988,
		periode_construction: '1983-1988',
		type_installation_chauffage: 'individuel',
		type_energie_principale_chauffage: 'électricité',
		qualite_isolation_enveloppe: 'moyenne',
		conso_5_usages_ep: 12345,
		emission_ges_5_usages: 456,
		cout_total_5_usages: 1234,
		validity_by_date: 'unknown',
		association: 'source_ban_equality',
		address_check: 'unverified',
		source: 'ADEME',
		source_coverage: 'unknown',
		graph_state: 'not_checked',
		imported_at: '2026-08-01T12:00:00Z',
		numero_dpe: 'ADEME-FORBIDDEN',
		identifiant_ban: 'BAN-FORBIDDEN',
		adresse: 'ADDRESS-FORBIDDEN',
		complement_adresse: 'COMPLEMENT-FORBIDDEN',
		longitude: -1.5,
		etage: 3,
		porte: 'gauche',
		id_technique: 'TECH-FORBIDDEN',
		...patch
	};
}

function attributionPayload(patch: Record<string, unknown> = {}): Record<string, unknown> {
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

beforeEach(() => apiMock.mockReset());

function lifecyclePayload(patch: Record<string, unknown> = {}) {
	return {
		source_state: 'present', source_snapshot_date: '2026-09-07', source_scope: '44/85',
		regimes: ['existant', 'neuf'], replacement_state: 'replaced', graph_issues: ['missing_target'],
		validity: 'unknown', as_of_date: '2026-09-08', revision_status: 'unverified',
		revision_kind: null, revision_date: null, detail_availability: 'available', ...patch
	};
}

describe('closed lifecycle projection', () => {
	function pageWithLifecycle(lifecycle: unknown = lifecyclePayload(), patch = {}) {
		const base = inventoryPayload();
		return inventoryPayload({ as_of_date: '2026-09-08', items: [
			{ ...(base.items as Record<string, unknown>[])[0], lifecycle, ...patch }
		] });
	}
	it('keeps the complete closed lifecycle and server date through repeated projection', () => {
		const result = projectDpeInventoryPage(pageWithLifecycle());
		expect(result.as_of_date).toBe('2026-09-08');
		expect(result.items[0].lifecycle).toEqual(lifecyclePayload());
		expect(projectDpeInventoryPage(result)).toEqual(result);
		expect(JSON.stringify(result)).not.toContain('FORBIDDEN');
	});
	it('keeps old V2 documents readable with an explicitly unavailable lifecycle', () => {
		expect(projectDpeInventoryPage(inventoryPayload()).items[0].lifecycle).toBeNull();
		expect(projectDpeConsultedDocument(documentPayload(), 'ctx-opaque').lifecycle).toBeNull();
	});
	it.each([
		{ source_state: 'PRIVATE-NUMERO' }, { replacement_state: { ban: 'PRIVATE-BAN' } },
		{ regimes: ['existant', 'PRIVATE-ADDRESS'] }, { regimes: ['existant', 'existant'] },
		{ graph_issues: ['PRIVATE-EDGE'] }, { source_scope: 'PRIVATE-COORDINATES' },
		{ source_snapshot_date: null }, { source_snapshot_date: '2026-09-09' },
		{ as_of_date: '2026-09-09' }, { as_of_date: '2026-02-30' },
		{ revision_kind: '2026' }, { revision_status: 'verified_attestation' },
		{ detail_availability: 'source_reconciled' }, { validity: 'expired' },
		{ private_nested: { numero: 'PRIVATE-NUMERO' } }, { graph_issues: undefined }
	])('rejects a malformed whole lifecycle without re-emitting any partial object: %o', (patch) => {
		const result = projectDpeInventoryPage(pageWithLifecycle(lifecyclePayload(patch)));
		expect(result.items[0].lifecycle).toBeNull();
		expect(projectDpeInventoryPage(result).items[0].lifecycle).toBeNull();
		expect(JSON.stringify(result)).not.toContain('PRIVATE-');
	});
	it.each([
		['inconsistent_dates', '2025-03-15', '2020-01-01'],
		['future_establishment', '2027-01-01', '2037-01-01'],
		['unknown', null, '2030-01-01']
	])('accepts unknown legacy validity with safe dates for %s', (validity, established, end) => {
		const result = projectDpeConsultedDocument(documentPayload({
			lifecycle: lifecyclePayload({ validity }), date_etablissement_dpe: established,
			date_fin_validite_dpe: end, validity_by_date: 'unknown'
		}), 'ctx-opaque');
		expect(result.lifecycle?.validity).toBe(validity);
		expect(result.validity_by_date).toBe('unknown');
	});
	it('purges all historic descriptive fields from a reconciled document and row', () => {
		const lifecycle = lifecyclePayload({ source_state: 'absent_from_complete_scope', detail_availability: 'source_reconciled' });
		const result = projectDpeConsultedDocument(documentPayload({ lifecycle }), 'ctx-opaque');
		for (const key of ['type_batiment', 'surface_habitable', 'etiquette_dpe', 'etiquette_ges',
			'date_etablissement_dpe', 'date_fin_validite_dpe', 'annee_construction', 'periode_construction',
			'type_installation_chauffage', 'type_energie_principale_chauffage', 'qualite_isolation_enveloppe',
			'conso_5_usages_ep', 'emission_ges_5_usages', 'cout_total_5_usages']) {
			expect(result[key as keyof typeof result]).toBeNull();
		}
		expect(result.lifecycle).toEqual(lifecycle);
		expect(projectDpeInventoryPage(pageWithLifecycle(lifecycle)).items[0].etiquette_dpe).toBeNull();
	});
	it('never revives tombstone payload when another lifecycle field is malformed', () => {
		const result = projectDpeConsultedDocument(documentPayload({ lifecycle: lifecyclePayload({
			source_state: 'absent_from_complete_scope', graph_issues: ['PRIVATE-EDGE']
		}) }), 'ctx-opaque');
		expect(result.lifecycle).toBeNull();
		expect(result.surface_habitable).toBeNull();
		expect(result.conso_5_usages_ep).toBeNull();
	});
});

describe('DPE inventory public contract', () => {
	it('exports fixed routes, bounds, units and error statuses', () => {
		expect(DPE_INVENTORY_ROUTE).toBe('/v1/bien/dpe/inventaire');
		expect(DPE_DOCUMENT_ROUTE).toBe('/v1/bien/dpe/document');
		expect(DPE_INVENTORY_DEFAULT_PAGE_SIZE).toBe(20);
		expect(DPE_INVENTORY_MAX_PAGE_SIZE).toBe(50);
		expect(DPE_INVENTORY_ERROR_STATUS).toEqual({
			invalid_reference: 400,
			refresh_required: 409,
			temporary_unavailable: 503
		});
		expect(DPE_DOCUMENT_UNITS.surface_habitable).toBe('m² habitables DPE');
	});

	it('projects every public field and drops identity/KPI sentinels', () => {
		const projected = projectDpeInventoryPage(inventoryPayload());
		expect(projected).toEqual({
			version: 2,
			as_of_date: null,
			state: 'ready',
			context_ref: 'ctx-opaque',
			items: [
				{
					document_ref: 'doc-opaque',
					type_batiment: 'appartement',
					surface_habitable: null,
					etiquette_dpe: 'D',
					etiquette_ges: null,
					date_etablissement_dpe: '2025-03-15',
					date_fin_validite_dpe: null,
					lifecycle: null
				}
			],
			total: 1,
			distribution,
			page_size: 20,
			has_more: false,
			next_cursor: null,
			read_state: 'complete',
			association: 'source_ban_equality',
			address_check: 'unverified',
			source: 'ADEME',
			source_coverage: 'unknown',
			graph_state: 'not_checked',
			imported_at: null
		});
		expect(JSON.stringify(projected)).not.toMatch(/FORBIDDEN/);
	});

	it('accepts an explicit empty inventory', () => {
		const empty = projectDpeInventoryPage(
			inventoryPayload({
				state: 'absent',
				items: [],
				total: 0,
				distribution: { ...distribution, D: 0 }
			})
		);
		expect(empty.state).toBe('absent');
		expect(empty.total).toBe(0);
		expect(empty.items).toEqual([]);
	});

	it.each([
		['unknown version', { version: 3 }],
		['unknown state', { state: 'exact' }],
		['page above maximum', { page_size: 51 }],
		['cursor without more results', { next_cursor: 'cursor' }],
		['blank ready context', { context_ref: '   ' }],
		['blank next cursor', { has_more: true, next_cursor: '   ' }],
		[
			'nonready pagination',
			{
				state: 'unavailable',
				items: [],
				total: null,
				has_more: true,
				next_cursor: 'x'
			}
		],
		[
			'absent unconfirmed total',
			{
				state: 'absent',
				items: [],
				total: null,
				distribution: { ...distribution, D: 0 }
			}
		],
		[
			'identity state with context',
			{
				state: 'identity_unavailable',
				items: [],
				total: null,
				distribution: { ...distribution, D: 0 }
			}
		]
	])('rejects %s', (_label, patch) => {
		expect(() => projectDpeInventoryPage(inventoryPayload(patch))).toThrow(
			DpeInventoryContractError
		);
	});

	it('accepts identity_unavailable only without context and confirmed totals', () => {
		const page = projectDpeInventoryPage(
			inventoryPayload({
				state: 'identity_unavailable',
				context_ref: null,
				items: [],
				total: null,
				distribution: { ...distribution, D: 0 }
			})
		);
		expect(page.state).toBe('identity_unavailable');
	});
});

describe('DPE consulted document public contract', () => {
	it('projects the complete allowlist and drops source identity selectors', () => {
		const projected = projectDpeConsultedDocument(documentPayload(), 'ctx-opaque');
		expect(Object.keys(projected)).toEqual([
			'version',
			'context_ref',
			'document_ref',
			'type_batiment',
			'surface_habitable',
			'etiquette_dpe',
			'etiquette_ges',
			'date_etablissement_dpe',
			'date_fin_validite_dpe',
			'lifecycle',
			'annee_construction',
			'periode_construction',
			'type_installation_chauffage',
			'type_energie_principale_chauffage',
			'qualite_isolation_enveloppe',
			'conso_5_usages_ep',
			'emission_ges_5_usages',
			'cout_total_5_usages',
			'validity_by_date',
			'association',
			'address_check',
			'source',
			'source_coverage',
			'graph_state',
			'imported_at'
		]);
		expect(projected.annee_construction).toBe(1988);
		expect(projected.conso_5_usages_ep).toBe(12345);
		expect(JSON.stringify(projected)).not.toMatch(/FORBIDDEN/);
	});

	it('rejects a document issued for another context', () => {
		expect(() => projectDpeConsultedDocument(documentPayload(), 'ctx-other')).toThrowError(
			expect.objectContaining({ code: 'context_mismatch' })
		);
	});

	it('rejects blank opaque document and context references from the response', () => {
		expect(() =>
			projectDpeConsultedDocument(documentPayload({ context_ref: '   ' }), '   ')
		).toThrow(DpeInventoryContractError);
		expect(() =>
			projectDpeConsultedDocument(documentPayload({ document_ref: '   ' }), 'ctx-opaque')
		).toThrow(DpeInventoryContractError);
	});

	it('validates date shape and structural validity without replaying the server clock', () => {
		expect(() =>
			projectDpeConsultedDocument(
				documentPayload({
					date_fin_validite_dpe: '2020-01-01',
					validity_by_date: 'expired'
				}),
				'ctx-opaque'
			)
		).not.toThrow();
		expect(() =>
			projectDpeConsultedDocument(
				documentPayload({
					date_fin_validite_dpe: '2999-01-01',
					validity_by_date: 'not_expired_by_date'
				}),
				'ctx-opaque'
			)
		).not.toThrow();
		expect(() =>
			projectDpeConsultedDocument(documentPayload({ validity_by_date: 'expired' }), 'ctx-opaque')
		).toThrow(DpeInventoryContractError);
		expect(() =>
			projectDpeConsultedDocument(
				documentPayload({ date_fin_validite_dpe: '2020-01-01', validity_by_date: 'unknown' }),
				'ctx-opaque'
			)
		).not.toThrow();
		expect(() =>
			projectDpeConsultedDocument(
				documentPayload({ date_etablissement_dpe: 'not-a-date' }),
				'ctx-opaque'
			)
		).toThrow(DpeInventoryContractError);
	});
});

describe('V2 attribution projector', () => {
	it('accepts V2 unassigned field by field and strips extras', () => {
		const projected = projectDpeAttributionV2(
			attributionPayload({
				building_context: {
					count: 4,
					distribution: { C: 1, D: 3 },
					truncated: false,
					neighbor_id: 'FORBIDDEN'
				},
				selection: 'FORBIDDEN'
			})
		);
		expect(projected).toEqual({
			version: 2,
			status: 'unassigned',
			reason: 'dwelling_identity_unproven',
			diagnostic: null,
			candidates: [],
			building_context: {
				count: 4,
				distribution: { C: 1, D: 3 },
				truncated: false
			},
			graph_state: 'not_checked'
		});
		expect(JSON.stringify(projected)).not.toMatch(/FORBIDDEN/);
	});

	it.each([
		{ status: 'exact', diagnostic: { numero_dpe: 'ADEME' } },
		{ version: undefined },
		{ version: 3 },
		{ candidates: [{ document_ref: 'doc' }] },
		{
			building_context: { count: 1, distribution: { A: 2 }, truncated: false }
		},
		{
			building_context: { count: 1, distribution: { unknown: 1 }, truncated: false }
		},
		{
			building_context: { count: 1, distribution: { A: 1, private_id: 0 }, truncated: false }
		}
	])('rejects legacy, unknown and inconsistent payload %o', (patch) => {
		expect(() => projectDpeAttributionV2(attributionPayload(patch))).toThrow(
			DpeInventoryContractError
		);
	});

	it('converts a legacy exact to a non-attributive refresh state', () => {
		const projected = projectDpeAttributionForClient({
			status: 'exact',
			diagnostic: { numero_dpe: 'ADEME-FORBIDDEN' }
		});
		expect(projected).toEqual({
			version: 2,
			status: 'unavailable',
			reason: 'contract_unavailable',
			diagnostic: null,
			candidates: [],
			building_context: null,
			graph_state: 'not_checked'
		});
		expect(JSON.stringify(projected)).not.toContain('ADEME-FORBIDDEN');
	});
});

describe('authenticated inventory clients', () => {
	it('calls the fixed inventory route once with only opaque parameters', async () => {
		apiMock.mockResolvedValueOnce(inventoryPayload({ context_ref: 'ctx ?&' }));
		const instrumentedFetch = vi.fn() as unknown as typeof fetch;
		const result = await getDpeInventory('ctx ?&', 'cursor /+', 23, instrumentedFetch);
		const [path, init, fetchImpl] = apiMock.mock.calls[0];
		const url = new URL(path, 'https://example.test');
		expect(url.pathname).toBe(DPE_INVENTORY_ROUTE);
		expect([...url.searchParams.keys()]).toEqual(['contexte', 'curseur', 'limite']);
		expect(url.searchParams.get('contexte')).toBe('ctx ?&');
		expect(url.searchParams.get('curseur')).toBe('cursor /+');
		expect(url.searchParams.get('limite')).toBe('23');
		expect(init).toEqual({ cache: 'no-store' });
		expect(fetchImpl).toBe(instrumentedFetch);
		expect(apiMock).toHaveBeenCalledTimes(1);
		expect(result.items[0].document_ref).toBe('doc-opaque');
	});

	it('uses the default page size and omits a missing cursor', async () => {
		apiMock.mockResolvedValueOnce(inventoryPayload({ context_ref: 'ctx' }));
		await getDpeInventory('ctx');
		const url = new URL(apiMock.mock.calls[0][0], 'https://example.test');
		expect(url.searchParams.get('limite')).toBe('20');
		expect(url.searchParams.has('curseur')).toBe(false);
	});

	it('returns an empty inventory as a successful explicit result', async () => {
		apiMock.mockResolvedValueOnce(
			inventoryPayload({
				state: 'absent',
				context_ref: 'ctx',
				items: [],
				total: 0,
				distribution: { ...distribution, D: 0 }
			})
		);
		await expect(getDpeInventory('ctx')).resolves.toMatchObject({
			state: 'absent',
			total: 0,
			items: []
		});
	});

	it('calls the document route once and validates the returned context', async () => {
		apiMock.mockResolvedValueOnce(documentPayload({ document_ref: 'doc ?&' }));
		const result = await getDpeDocument('ctx-opaque', 'doc ?&');
		const url = new URL(apiMock.mock.calls[0][0], 'https://example.test');
		expect(url.pathname).toBe(DPE_DOCUMENT_ROUTE);
		expect([...url.searchParams.keys()]).toEqual(['contexte', 'reference']);
		expect(url.searchParams.get('contexte')).toBe('ctx-opaque');
		expect(url.searchParams.get('reference')).toBe('doc ?&');
		expect(result.document_ref).toBe('doc ?&');
		expect(apiMock).toHaveBeenCalledTimes(1);
	});

	it('rejects a response that crosses the requested opaque target', async () => {
		apiMock.mockResolvedValueOnce(inventoryPayload({ context_ref: 'another-context' }));
		await expect(getDpeInventory('ctx')).rejects.toMatchObject({
			code: 'context_mismatch'
		});

		apiMock.mockResolvedValueOnce(documentPayload({ document_ref: 'another-document' }));
		await expect(getDpeDocument('ctx-opaque', 'doc')).rejects.toMatchObject({
			code: 'invalid_payload'
		});
	});

	it.each([
		[409, 'refresh_required'],
		[503, 'temporary_unavailable'],
		[400, 'invalid_reference']
	] as const)(
		'maps HTTP %s to fixed error %s without retry or reflected body',
		async (status, code) => {
			apiMock.mockRejectedValueOnce(new ApiError(status, 'UPSTREAM-SECRET-REFERENCE'));
			const error = await getDpeInventory('ctx').catch((reason) => reason);
			expect(error).toBeInstanceOf(DpeInventoryRequestError);
			expect(error).toMatchObject({ code, status });
			expect(error.message).not.toContain('UPSTREAM-SECRET-REFERENCE');
			expect(apiMock).toHaveBeenCalledTimes(1);
		}
	);

	it('sanitizes an unexpected API failure', async () => {
		apiMock.mockRejectedValueOnce(new ApiError(422, 'OPAQUE-SECRET'));
		const error = await getDpeDocument('ctx', 'doc').catch((reason) => reason);
		expect(error.message).toBe('La consultation DPE a échoué.');
		expect(error.message).not.toContain('OPAQUE-SECRET');
		expect(apiMock).toHaveBeenCalledTimes(1);
	});

	it('does not reflect an unexpected transport error containing the opaque URL', async () => {
		apiMock.mockRejectedValueOnce(new Error('network https://host/?contexte=OPAQUE-SECRET'));
		const error = await getDpeInventory('ctx').catch((reason) => reason);
		expect(error.message).toBe('La consultation DPE a échoué.');
		expect(error.message).not.toContain('OPAQUE-SECRET');
		expect(apiMock).toHaveBeenCalledTimes(1);
	});

	it('delegates authentication to api() and persists or logs no opaque token', () => {
		const source = readFileSync(resolvePath(process.cwd(), 'src/lib/api/dpe-inventory.ts'), 'utf8');
		expect(source).toContain('api<unknown>');
		expect(source).not.toMatch(/\bfetch\s*\(/);
		expect(source).not.toMatch(/localStorage|sessionStorage|console\./);
		expect(source).not.toContain('...project');
	});
});
