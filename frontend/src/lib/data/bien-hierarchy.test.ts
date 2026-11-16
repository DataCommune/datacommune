import { describe, expect, it } from 'vitest';

import type {
	DpeAttributionClient,
	DpeInventoryPage
} from '$lib/api/dpe-inventory';
import type { ResultatMultiResolution } from '$lib/api/resolve';
import type { FicheGrid } from '$lib/types/fiche-grid';
import { mapBienHierarchy } from './bien-hierarchy';

const distribution = { A: 0, B: 2, C: 2, D: 0, E: 0, F: 0, G: 0, unknown: 3 };

function multi(overrides: Partial<ResultatMultiResolution> = {}): ResultatMultiResolution {
	return {
		type_detecte: 'adresse', latitude: 47.2, longitude: -1.5,
		adresse: '8 rue de la Source', id_parcelle: null, commune: 'Nantes',
		code_postal: '44000', code_insee: '44109', ban_id: 'ban-only', score: 0.99,
		datasets: {}, erreurs: [], avertissements: [], ...overrides
	};
}

function inventory(overrides: Partial<DpeInventoryPage> = {}): DpeInventoryPage {
	return {
		version: 2, state: 'ready', context_ref: 'opaque-context', items: [], total: 7,
		distribution, page_size: 2, has_more: true, next_cursor: 'opaque-cursor',
		read_state: 'complete', association: 'source_ban_equality', address_check: 'unverified',
		source: 'ADEME', source_coverage: 'unknown', graph_state: 'not_checked', imported_at: null,
		...overrides
	};
}

function attribution(
	overrides: Partial<Extract<DpeAttributionClient, { status: 'unassigned' }>> = {}
): DpeAttributionClient {
	return {
		version: 2, status: 'unassigned', reason: 'dwelling_identity_unproven', diagnostic: null,
		candidates: [], building_context: null, graph_state: 'not_checked', ...overrides
	};
}

function row(model: ReturnType<typeof mapBienHierarchy>, id: string) {
	const found = model.grid.rows.find((candidate) => candidate.concept.id === id);
	if (!found) throw new Error(`Missing test row ${id}`);
	return found;
}

describe('mapBienHierarchy', () => {
	it('fixe trois mailles et ne transforme jamais une clé BAN en bâtiment ou logement', () => {
		const model = mapBienHierarchy(multi(), inventory(), attribution(), null);

		expect(model.grid.columns.map(({ id, entity, grain }) => ({ id, entity, grain }))).toEqual([
			{ id: 'dwelling', entity: 'bien', grain: 'Logement' },
			{ id: 'context', entity: 'adresse', grain: 'Adresse' },
			{ id: 'commune', entity: 'commune', grain: 'Commune' }
		]);
		expect(model.grid.columns.some((column) => column.entity === 'batiment')).toBe(false);
		expect(JSON.stringify(model.grid.columns)).not.toContain('ban-only');
	});

	it('conserve le logement indisponible malgré inventaire, classe dominante et document consulté injecté', () => {
		const poisoned = {
			...attribution(), status: 'building', diagnostic: { etiquette_dpe: 'B', surface_habitable: 72 }
		} as unknown as DpeAttributionClient;
		const model = mapBienHierarchy(multi(), inventory(), poisoned, null);

		for (const conceptId of ['dpe-attribution', 'dpe-document-count', 'dpe-distribution']) {
			expect(row(model, conceptId).cells.dwelling).toMatchObject({ kind: 'absent' });
			expect(row(model, conceptId).cells.dwelling).not.toHaveProperty('display');
		}
		expect(JSON.stringify(model.grid)).not.toContain('72');
	});

	it('lit la distribution complète du payload, conserve inconnues, ex aequo et pagination', () => {
		const model = mapBienHierarchy(multi(), inventory({
			items: [{ document_ref: 'page-only', type_batiment: null, surface_habitable: null,
				etiquette_dpe: 'G', etiquette_ges: null, date_etablissement_dpe: null,
				date_fin_validite_dpe: null }]
		}), attribution(), null);
		const count = row(model, 'dpe-document-count').cells.context;
		const classes = row(model, 'dpe-distribution').cells.context;

		expect(count).toMatchObject({ kind: 'value', display: 7, unit: 'documents connus', grain: 'Adresse' });
		expect(classes).toMatchObject({ kind: 'value', display: '2 B · 2 C · 3 classes non disponibles' });
		expect(classes.reserves).toEqual(expect.arrayContaining([
			'Lecture complète du périmètre importé ; la couverture de tous les diagnostics existants n’est pas garantie.',
			'Couverture de la source ADEME : inconnue.'
		]));
		expect(JSON.stringify(classes)).not.toContain('1 G');
	});

	it('distingue zéro mesuré, total inconnu, lecture partielle et absence de payload', () => {
		const zero = row(mapBienHierarchy(multi(), inventory({
			total: 0, distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
			items: [], has_more: false, next_cursor: null
		}), attribution(), null), 'dpe-document-count').cells.context;
		const unknown = row(mapBienHierarchy(multi(), inventory({
			total: null, read_state: 'partial', distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 1 }
		}), attribution(), null), 'dpe-document-count').cells.context;
		const missing = row(mapBienHierarchy(multi(), null, attribution(), null), 'dpe-document-count').cells.context;

		expect(zero).toMatchObject({ kind: 'value', display: 0, unit: 'documents connus', measuredZero: true });
		expect(unknown).toMatchObject({ kind: 'absent', state: 'not_reported' });
		expect(unknown.reserves).toContain('Lecture partielle du périmètre importé.');
		expect(missing).toMatchObject({ kind: 'absent', state: 'not_reported' });
		expect(missing.details.raisonAbsence).toContain('non publié');
	});

	it('respecte les états d’inventaire avant les nombres transportés', () => {
		const unavailable = row(mapBienHierarchy(multi(), inventory({ state: 'unavailable', total: 0 }), attribution(), null), 'dpe-document-count').cells.context;
		const identity = row(mapBienHierarchy(multi(), inventory({ state: 'identity_unavailable', total: 0 }), attribution(), null), 'dpe-document-count').cells.context;
		const absentZero = row(mapBienHierarchy(multi(), inventory({ state: 'absent', total: 0 }), attribution(), null), 'dpe-document-count').cells.context;
		const absentUnknown = row(mapBienHierarchy(multi(), inventory({ state: 'absent', total: null }), attribution(), null), 'dpe-document-count').cells.context;

		expect(unavailable).toMatchObject({ kind: 'absent', state: 'temporary_error' });
		expect(identity).toMatchObject({ kind: 'absent', state: 'not_reported' });
		expect(identity.details.raisonAbsence).toContain('identité de l’adresse');
		expect(absentZero).toMatchObject({ kind: 'value', display: 0, measuredZero: true });
		expect(absentUnknown).toMatchObject({ kind: 'absent', state: 'not_reported' });
	});

	it('distingue attribution absente et panne sans emprunter un attribut documentaire', () => {
		const absent: DpeAttributionClient = { version: 2, status: 'absent', reason: 'not_reported',
			diagnostic: null, candidates: [], building_context: null, graph_state: 'not_checked' };
		const error: DpeAttributionClient = { version: 2, status: 'error', reason: 'temporary_error',
			diagnostic: null, candidates: [], building_context: null, graph_state: 'not_checked' };
		const unavailable: DpeAttributionClient = { version: 2, status: 'unavailable', reason: 'contract_unavailable',
			diagnostic: null, candidates: [], building_context: null, graph_state: 'not_checked' };

		expect(row(mapBienHierarchy(multi(), inventory(), absent, null), 'dpe-attribution').cells.dwelling)
			.toMatchObject({ kind: 'absent', state: 'not_reported' });
		expect(row(mapBienHierarchy(multi(), inventory(), error, null), 'dpe-attribution').cells.dwelling)
			.toMatchObject({ kind: 'absent', state: 'temporary_error' });
		expect(row(mapBienHierarchy(multi(), inventory(), unavailable, null), 'dpe-attribution').cells.dwelling)
			.toMatchObject({ kind: 'absent', state: 'not_reported' });
	});

	it('présente le contexte multi-porteurs RNB comme indicatif et recopie les cellules communales', () => {
		const communal = {
			id: 'qualified-communal',
			columns: [{ id: 'source-commune', entity: 'commune', scope: 'commune', grain: 'Commune', label: 'Nantes', code: '44109' }],
			rows: [{
				concept: { id: 'commune-price', label: 'Prix médian des maisons', definition: 'Médiane publiée', unit: '€/m²' },
				cells: { 'source-commune': {
					kind: 'value', state: 'value', display: '3 210', grain: 'Commune', unit: '€/m²', unitInline: false,
					estDatable: true, millesime: '2025', source: { nom: 'DVF', url: null }, reserves: ['12 mutations'],
					details: { definition: 'Médiane publiée', ecart: null, tolerance: null, reference: null, raisonAbsence: null }
				} }
			}]
		} satisfies FicheGrid;
		const model = mapBienHierarchy(multi(), inventory(), attribution({
			building_context: { count: 5, distribution: { A: 1, B: 1 }, truncated: true }
		}), communal);

		const context = row(model, 'dpe-rnb-context').cells.context;
		expect(context).toMatchObject({ kind: 'context', text: expect.stringContaining(
			'Autres adresses liées via le RNB — contexte indicatif · Répartition des diagnostics connus : 1 A · 1 B'
		), grain: 'Adresse' });
		expect(context.reserves).toEqual(expect.arrayContaining([
			'5 documents terminaux dans ce contexte.',
			'L’adresse consultée est exclue de ce contexte.',
			'Répartition A–G incomplète : les classes inconnues ne sont pas publiées explicitement.',
			'Contexte tronqué : les limites ou le graphe documentaire sont incomplets.'
		]));
		expect(model.grid.columns[1].entity).toBe('adresse');
		expect(row(model, 'commune-price').cells.commune).toEqual(communal.rows[0].cells['source-commune']);
		expect(row(model, 'commune-price').cells.dwelling.details.raisonAbsence).toContain('comparaison');
	});

	it('ne qualifie pas de terminaux les lignes d’un graphe incohérent', () => {
		const model = mapBienHierarchy(multi(), inventory(), attribution({
			building_context: { count: 5, distribution: { A: 1 }, truncated: false },
			graph_state: 'incoherent'
		}), null);

		expect(row(model, 'dpe-rnb-context').cells.context.reserves).toContain(
			'5 lignes documentaires dans ce contexte ; le graphe incohérent ne permet pas de les qualifier de terminales.'
		);
		expect(model.scopeNotes).toEqual(expect.arrayContaining([
			'Aucun bâtiment unique n’est prouvé par ce contexte RNB.',
			'Les autres clés BAN peuvent désigner des adresses différentes de l’adresse consultée.'
		]));
	});

	it('ignore un ancien building_context qui ne porte pas le contrat V2', () => {
		const legacy = { ...attribution({ building_context: { count: 1, distribution: { A: 1 }, truncated: false } }), version: 1 } as unknown as DpeAttributionClient;
		const model = mapBienHierarchy(multi(), inventory(), legacy, null);

		expect(model.grid.rows.some((candidate) => candidate.concept.id === 'dpe-rnb-context')).toBe(false);
	});
});
