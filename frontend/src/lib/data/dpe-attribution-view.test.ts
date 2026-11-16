import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve as resolvePath } from 'node:path';
import {
	attributionInattendue,
	deriveDpeAttributionView,
	formaterDistributionBatiment
} from './dpe-attribution-view';

const HREF = '/bien/adresse-test';

function v2(patch: Record<string, unknown> = {}): Record<string, unknown> {
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

describe('deriveDpeAttributionView V2', () => {
	it('keeps an unassigned inventory neutral for KPI and exports', () => {
		const view = deriveDpeAttributionView(v2(), { hrefBase: HREF });
		expect(view.status).toBe('unassigned');
		expect(view.reason).toBe('dwelling_identity_unproven');
		expect(view.ficheSelection).toEqual({
			diagnostic: null,
			gapState: 'out_of_coverage'
		});
		expect(view.candidats).toEqual([]);
		expect(view.candidatChoisi).toBeNull();
	});

	it.each([
		{ dpe: '1234-RAW-ADEME' },
		{ dpe: '1234-RAW-ADEME', immeuble: true },
		{ dpe: '1234-RAW-ADEME', typeBien: 'appartement' as const }
	])('makes legacy query inputs inert: %o', (options) => {
		const view = deriveDpeAttributionView(v2(), { hrefBase: HREF, ...options });
		expect(view.ficheSelection.diagnostic).toBeNull();
		expect(view.ancre).toBe('aucune');
		expect(view.retourHref).toBeNull();
		expect(JSON.stringify(view)).not.toContain('1234-RAW-ADEME');
	});

	it('preserves identity-unavailable separately from a reported absence', () => {
		const identity = deriveDpeAttributionView(
			v2({ status: 'absent', reason: 'identity_unavailable' }),
			{ hrefBase: HREF }
		);
		const absent = deriveDpeAttributionView(v2({ status: 'absent', reason: 'not_reported' }), {
			hrefBase: HREF
		});
		expect(identity.status).toBe('absent');
		expect(identity.reason).toBe('identity_unavailable');
		expect(identity.absenceTexte).toContain('identité');
		expect(absent.reason).toBe('not_reported');
		expect(absent.absenceTexte).toBe('Aucun DPE rattaché exactement à cette adresse');
	});

	it('preserves a temporary outage and never relabels it as absence', () => {
		const view = deriveDpeAttributionView(v2({ status: 'error', reason: 'temporary_error' }), {
			hrefBase: HREF
		});
		expect(view.status).toBe('error');
		expect(view.absenceTexte).toBeNull();
		expect(view.erreurConstat).toContain('indisponibles');
		expect(view.ficheSelection).toEqual({
			diagnostic: null,
			gapState: 'temporary_error'
		});
	});

	it('renders the local unavailable state without a legacy status cast', () => {
		const view = deriveDpeAttributionView(v2({ version: 3 }), { hrefBase: HREF });
		expect(view.status).toBe('unavailable');
		expect(view.reason).toBe('contract_unavailable');
		expect(view.erreurConstat).toBe('Contrat DPE à actualiser');
		expect(view.ficheSelection).toEqual({
			diagnostic: null,
			gapState: 'temporary_error'
		});
	});

	it('preserves only the aggregate building context and graph warning', () => {
		const view = deriveDpeAttributionView(
			v2({
				building_context: {
					count: 5,
					distribution: { G: 1, A: 2, D: 2 },
					truncated: true,
					neighbor: 'forbidden'
				},
				graph_state: 'incoherent'
			}),
			{ hrefBase: HREF }
		);
		expect(view.graph_state).toBe('incoherent');
		expect(view.building_context).toEqual({
			count: 5,
			distribution: { A: 2, D: 2, G: 1 },
			truncated: true
		});
		expect(view.batiment?.distribution).toBe(
			'Répartition des diagnostics connus : 2 A · 2 D · 1 G'
		);
		expect(view.batiment?.titre).toBe(
			'Autres adresses liées via le RNB — contexte indicatif'
		);
		expect(view.batiment?.effectif).toBe(
			'5 diagnostics connus sur les autres adresses liées'
		);
		expect(view.batiment?.tronque).toBe(true);
		expect(JSON.stringify(view)).not.toContain('forbidden');
	});

	it.each([
		v2({ status: 'error', reason: 'temporary_error', building_context: { count: 2, distribution: { D: 2 }, truncated: false } }),
		{ status: 'building', building_context: { count: 2, distribution: { D: 2 }, truncated: false } }
	])('never exposes contextual addresses for errors or unversioned legacy states: %o', (payload) => {
		const view = deriveDpeAttributionView(payload, { hrefBase: HREF });
		expect(view.building_context).toBeNull();
		expect(view.batiment).toBeNull();
	});
});

describe('legacy and malformed contracts fail closed', () => {
	it.each([
		{
			status: 'exact',
			diagnostic: { numero_dpe: 'ADEME-SECRET', etiquette_dpe: 'A' },
			candidates: []
		},
		{
			status: 'multiple',
			diagnostic: null,
			candidates: [{ numero_dpe: 'ADEME-SECRET', etage: 3, porte: 'gauche' }]
		},
		{
			status: 'building',
			diagnostic: null,
			candidates: [],
			building_context: { count: 1, distribution: { A: 1 }, truncated: false }
		},
		v2({ version: 3 }),
		v2({ status: 'exact', diagnostic: { numero_dpe: 'ADEME-SECRET' } }),
		v2({ candidates: [{ numero_dpe: 'ADEME-SECRET' }] }),
		v2({
			building_context: {
				count: 1,
				distribution: { A: 1, raw_id: 'ADEME-SECRET' },
				truncated: false
			}
		})
	])('returns a refresh state without carrying legacy identity: %o', (payload) => {
		const view = deriveDpeAttributionView(payload, {
			hrefBase: HREF,
			dpe: 'ADEME-SECRET'
		});
		expect(view.status).toBe('unavailable');
		expect(view.reason).toBe('contract_unavailable');
		expect(view.ficheSelection).toEqual({
			diagnostic: null,
			gapState: 'temporary_error'
		});
		expect(view.candidats).toEqual([]);
		expect(view.batiment).toBeNull();
		expect(JSON.stringify(view)).not.toContain('ADEME-SECRET');
	});

	it('contains no candidate-to-diagnostic implementation path', () => {
		const source = readFileSync(
			resolvePath(process.cwd(), 'src/lib/data/dpe-attribution-view.ts'),
			'utf8'
		);
		expect(source).not.toContain('diagnosticDepuisCandidat');
		expect(source).not.toContain('.numero_dpe');
		expect(source).not.toContain('.candidates');
		expect(source).not.toMatch(/\bfetch\s*\(/);
		expect(source).not.toMatch(/\bapi\s*\(/);
		expect(source).not.toMatch(/localStorage|sessionStorage/);
		expect(source).not.toContain('as unknown as DpeAttributionStatus');
		expect(source).not.toContain('transitionalStatus');
	});
});

describe('aggregate formatter', () => {
	it('formats only the backend A-G vocabulary in official order without electing a class', () => {
		expect(formaterDistributionBatiment({ G: 1, A: 2, D: 3, unknown: 4 })).toBe(
			'Répartition des diagnostics connus : 2 A · 3 D · 1 G'
		);
		expect(formaterDistributionBatiment({ unknown: 2 })).toBeNull();
		expect(formaterDistributionBatiment({})).toBeNull();
	});

	it('keeps the explicit exhaustive assertion available', () => {
		expect(() => attributionInattendue({ status: 'future' } as never)).toThrow(/future/);
	});
});
