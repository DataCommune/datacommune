import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMock = vi.fn();
vi.mock('./client', async () => {
	const actual = await vi.importActual<typeof import('./client')>('./client');
	return { ...actual, api: (...args: unknown[]) => apiMock(...args) };
});

import {
	DpeMatchingContractError,
	DpeMatchingRequestError,
	createDpeMatchingClient,
	isDpeMatchingPreviewEnvironment,
	projectDpeMatchResult
} from './dpe-matching';

const inventory = {
	version: 2, state: 'ready', context_ref: 'ctx-a',
	items: [{
		document_ref: 'doc-a', type_batiment: 'Maison', surface_habitable: 80,
		etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement_dpe: '2024-01-02', date_fin_validite_dpe: '2034-01-01'
	}],
	total: 1, distribution: { A: 0, B: 0, C: 0, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
	page_size: 20, has_more: false, next_cursor: null, read_state: 'complete',
	association: 'source_ban_equality', address_check: 'unverified', source: 'ADEME',
	source_coverage: 'unknown', graph_state: 'not_checked', imported_at: null
};
const attribution = {
	version: 2, status: 'unassigned', reason: 'dwelling_identity_unproven',
	diagnostic: null, candidates: [], building_context: { count: 1, distribution: { D: 1 }, truncated: false }, graph_state: 'not_checked'
};
const payload = (patch: Record<string, unknown> = {}) => ({
	inventory, attribution, rule_version: 'dpe-factual-comparison-v1',
	comparisons: [{
		document_ref: 'doc-a', criteria: [
			{ criterion: 'type_bien', state: 'matching', target: 'maison', source: 'maison' },
			{ criterion: 'surface', state: 'matching', target: 80, source: 80, target_nature: 'habitable_dpe', source_nature: 'habitable_dpe', delta: 0 }
		], blockers: []
	}], limits: ['dwelling_identity_unproven'], ...patch
});

describe('preview environment guard', () => {
	it('allows local development and requires an explicit dev deployment opt-in', () => {
		expect(isDpeMatchingPreviewEnvironment('localhost', true, false)).toBe(true);
		expect(isDpeMatchingPreviewEnvironment('dev.datacommune.fr', false, false)).toBe(false);
		expect(isDpeMatchingPreviewEnvironment('dev.datacommune.fr', false, true)).toBe(true);
	});
	it('always refuses production hosts even with the opt-in', () => {
		expect(isDpeMatchingPreviewEnvironment('datacommune.fr', false, true)).toBe(false);
		expect(isDpeMatchingPreviewEnvironment('www.datacommune.fr', true, true)).toBe(false);
	});
});

describe('dpe matching contract', () => {
	beforeEach(() => apiMock.mockReset());
	it('projects the closed non-attributive response', () => {
		expect(projectDpeMatchResult(payload(), 'ctx-a').comparisons).toHaveLength(1);
	});
	it.each([
		['unknown field', { private_floor: 3 }],
		['unknown blocker', { comparisons: [{ ...payload().comparisons[0], blockers: ['invented_blocker'] }] }],
		['positive attribution', { attribution: { ...attribution, status: 'exact', diagnostic: {}, candidates: ['doc-a'] } }],
		['discordant context', { inventory: { ...inventory, context_ref: 'ctx-b' } }],
		['removed candidate', { comparisons: [] }]
	])('refuses %s', (_name, patch) => {
		expect(() => projectDpeMatchResult(payload(patch), 'ctx-a')).toThrow(DpeMatchingContractError);
	});
	it('posts the private target only in a no-store body', async () => {
		apiMock.mockResolvedValue(payload());
		const client = createDpeMatchingClient();
		await client.compare('ctx-a', { type_bien: 'maison', surface: { value: 80, nature: 'habitable_dpe' } });
		expect(apiMock.mock.calls[0][0]).toBe('/v1/bien/dpe/comparaison');
		expect(apiMock.mock.calls[0][0]).not.toContain('maison');
		expect(apiMock.mock.calls[0][1]).toMatchObject({ method: 'POST', cache: 'no-store' });
		expect(JSON.parse(apiMock.mock.calls[0][1].body)).toEqual({ contexte: 'ctx-a', limite: 20, cible: { type_bien: 'maison', surface: { value: 80, nature: 'habitable_dpe' } } });
	});
	it('ignores an old response after target correction', async () => {
		let release!: (value: unknown) => void;
		apiMock.mockReturnValueOnce(new Promise((resolve) => (release = resolve))).mockResolvedValueOnce(payload());
		const client = createDpeMatchingClient();
		const old = client.compare('ctx-a', { type_bien: 'maison' });
		const current = client.compare('ctx-a', { type_bien: 'appartement' });
		release(payload());
		expect(await old).toBeNull();
		expect(await current).not.toBeNull();
	});
	it('distinguishes a network failure from a factual absence', async () => {
		apiMock.mockImplementationOnce(async () => { throw new Error('offline'); });
		await expect(createDpeMatchingClient().compare('ctx-a', {})).rejects.toBeInstanceOf(DpeMatchingRequestError);
	});
});
