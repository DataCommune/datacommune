import { describe, expect, it } from 'vitest';

import type { FicheGridCell, FicheGridRow } from '$lib/types/fiche-grid';
import type { BienHierarchyModel } from './bien-hierarchy';
import type { CommuneSynthese } from '$lib/api/commune';
import { mapBienCommuneContext } from './bien-commune-context';
import { mapBienHierarchy } from './bien-hierarchy';
import { presentBienHierarchy, withoutRepeatedHierarchyContext } from './bien-hierarchy-presentation';
import { COMMUNE_INDICATORS_85 } from './__fixtures__/commune-comparison-indicators';

const details = { definition: null, ecart: null, tolerance: null, reference: null, raisonAbsence: null };

function cell(reserves: readonly string[] = [], existing = false): FicheGridCell {
	return {
		kind: 'value', state: 'value', display: 12, grain: 'Commune', unit: null, unitInline: false,
		estDatable: true, millesime: '2025', source: { nom: 'Source', url: null }, reserves, details,
		...(existing ? { criticalSummary: { facts: ['Fait fournisseur'], warnings: ['Réserve fournisseur'] } } : {})
	};
}

function absent(): Extract<FicheGridCell, { kind: 'absent' }> {
	return {
		kind: 'absent', state: 'not_applicable', label: '—', explanation: 'Donnée communale',
		grain: 'Logement', unit: null, unitInline: false, estDatable: false, millesime: null,
		source: null, reserves: [], details
	};
}

function row(id: string, communeCell = cell()): FicheGridRow {
	return {
		concept: { id, label: id, definition: null, unit: null },
		cells: { dwelling: absent(), context: absent(), commune: communeCell }
	};
}

function model(): BienHierarchyModel {
	const ids = [
		'dpe-attribution', 'dpe-document-count', 'dpe-distribution', 'dpe-rnb-context',
		'bien-commune-context:dvf-maison-mediane-courante',
		'bien-commune-context:dvf-maison-variation',
		'bien-commune-context:dvf-appartement-mediane-courante',
		'bien-commune-context:dvf-appartement-variation',
		'bien-commune-context:context:dpe-mediane',
		'bien-commune-context:context:dpe-modes',
		'bien-commune-context:dpe-part-fg',
		'bien-commune-context:dvf-indicateurs-absence',
		'bien-commune-context:dpe-indicateurs-absence',
		'bien-commune-context:dpe-resultat-absence',
		'bien-commune-context:context:dvf-maison-effectif-courant',
		'bien-commune-context:context:dvf-maison-quantiles-courants',
		'bien-commune-context:dpe-population',
		'bien-commune-context:context:dpe-distribution',
		'bien-commune-context:dpe-periode'
	];
	const rich = cell([
		'Source : ADEME', 'Source(s) : ADEME · RNB', 'Producteur : ADEME', 'Licence : Etalab',
		'Référence : 2025', 'Publication source : 2026-01', 'Snapshot : 2026-02',
		'Import : 2026-03', 'Génération : g1', 'Calcul : 2026-04',
		'Période : 2024–2025', 'Effectif : 12', 'Population : diagnostics',
		'Couverture inconnue', 'Motif source', 'Graphe incohérent', 'Association non vérifiée'
	], true);
	const rows = ids.map((id) => row(id, id.endsWith('dvf-maison-mediane-courante') ? rich : cell()));
	rows.push({ concept: { id: 'secondary-empty-absence', label: 'absence', definition: null, unit: null }, cells: {
		dwelling: absent(), context: absent(), commune: absent()
	} });
	return {
		grid: { id: 'bien-hierarchy', columns: [
			{ id: 'dwelling', entity: 'bien', scope: 'bien', grain: 'Logement', label: 'Logement' },
			{ id: 'context', entity: 'adresse', scope: 'bien', grain: 'Adresse', label: 'Adresse' },
			{ id: 'commune', entity: 'commune', scope: 'commune', grain: 'Commune', label: 'Nantes', code: '44109' }
		], rows },
		scopeNotes: ['Réserve générale']
	};
}

describe('presentBienHierarchy', () => {
	it('ne retire du contexte secondaire que les cellules déjà présentées intégralement', () => {
		const source = model();
		const original = source.grid.rows.find((entry) => entry.concept.id.endsWith(':dpe-mediane'))!;
		const theme = { id: 'theme', columns: [source.grid.columns[2]], rows: [
			{ concept: { ...original.concept, id: 'theme:context:dpe-mediane' }, cells: { commune: structuredClone(original.cells.commune) } },
			{ concept: { ...original.concept, id: 'theme:dpe-mediane' }, cells: { commune: { ...original.cells.commune, reserves: ['Qualification supplémentaire'] } } },
			{ concept: { ...original.concept, id: 'theme:autre-indicateur' }, cells: { commune: structuredClone(original.cells.commune) } }
		] };
		const before = structuredClone(theme);
		expect(withoutRepeatedHierarchyContext(theme, source).rows).toEqual(theme.rows.slice(1));
		expect(theme).toEqual(before);
		expect(withoutRepeatedHierarchyContext(theme, null)).toEqual(theme);
	});
	it('partitionne tous les concepts exactement une fois avec une seule colonne secondaire', () => {
		const source = model();
		const result = presentBienHierarchy(source);
		const primary = result.primary.rows.map((entry) => entry.concept.id);
		const secondary = result.secondary.rows.map((entry) => entry.concept.id);

		expect(primary).toEqual(expect.arrayContaining([
			'dpe-attribution', 'dpe-document-count', 'dpe-distribution', 'dpe-rnb-context',
			'bien-commune-context:dvf-maison-mediane-courante',
			'bien-commune-context:dvf-appartement-variation',
			'bien-commune-context:context:dpe-mediane', 'bien-commune-context:dpe-part-fg',
			'bien-commune-context:dvf-indicateurs-absence', 'bien-commune-context:dpe-indicateurs-absence',
			'bien-commune-context:dpe-resultat-absence'
		]));
		expect(secondary).toEqual(expect.arrayContaining([
			'bien-commune-context:context:dvf-maison-effectif-courant',
			'bien-commune-context:context:dvf-maison-quantiles-courants',
			'bien-commune-context:dpe-population', 'bien-commune-context:context:dpe-distribution',
			'bien-commune-context:dpe-periode',
			'secondary-empty-absence'
		]));
		expect([...primary, ...secondary].sort()).toEqual(source.grid.rows.map((entry) => entry.concept.id).sort());
		expect(new Set([...primary, ...secondary]).size).toBe(source.grid.rows.length);
		expect(result.secondary.columns.map((column) => column.id)).toEqual(['commune']);
		expect(result.secondary.rows.every((entry) => Object.keys(entry.cells).join() === 'commune')).toBe(true);
	});

	it('respecte le résumé qualifié fourni et garde les réserves intégrales', () => {
		const source = model();
		const original = structuredClone(source);
		const result = presentBienHierarchy(source);
		const projected = result.primary.rows.find((entry) => entry.concept.id.endsWith('dvf-maison-mediane-courante'))!.cells.commune;

		expect(projected.reserves).toEqual(original.grid.rows[4].cells.commune.reserves);
		expect(projected.criticalSummary).toEqual({
			facts: ['Fait fournisseur'],
			warnings: ['Réserve fournisseur']
		});
		expect(source).toEqual(original);
	});

	it('ne réinflate pas les résumés réels du fournisseur dans la hiérarchie', () => {
		const communal = mapBienCommuneContext(COMMUNE_INDICATORS_85)!;
		const hierarchy = mapBienHierarchy(
			{ adresse: 'Adresse fournisseur', commune: 'Challans', code_insee: '85047' } as Parameters<typeof mapBienHierarchy>[0],
			null,
			{ version: 2, status: 'unassigned', reason: 'dwelling_identity_unproven', diagnostic: null,
				candidates: [], building_context: null, graph_state: 'not_checked' },
			communal
		);
		const sourceRow = hierarchy.grid.rows.find((entry) => entry.concept.id.endsWith(':dpe-mediane'))!;
		const sourceCell = sourceRow.cells.commune;
		const original = structuredClone(sourceCell);
		const sourceWarnings = sourceCell.criticalSummary!.warnings;

		expect(sourceWarnings.some((warning) => /expired|expiré/i.test(warning))).toBe(true);
		expect(sourceWarnings.some((warning) => /not_checked|non vérifi/i.test(warning))).toBe(true);
		expect(sourceWarnings.some((warning) => /traces retirées/i.test(warning))).toBe(true);
		expect(sourceWarnings.some((warning) => /ne représentent pas un décompte de logements/i.test(warning))).toBe(true);

		const projected = presentBienHierarchy(hierarchy);
		const screen = projected.primary.rows.find((entry) => entry.concept.id.endsWith(':dpe-mediane'))!.cells.commune;
		const printed = projected.print.rows.find((entry) => entry.concept.id.endsWith(':dpe-mediane:commune'))!.cells.scope;

		expect(screen.criticalSummary).toEqual(sourceCell.criticalSummary);
		expect(screen.reserves).toEqual(original.reserves);
		expect(printed.reserves).toEqual([...new Set([
			...original.reserves, ...original.criticalSummary!.facts, ...original.criticalSummary!.warnings
		])]);
		expect(sourceCell).toEqual(original);
	});

	it('replie les métadonnées d’une réserve composite sans masquer sa qualification statistique', () => {
		const composite = [
			'Population statistique : mutations résidentielles mono-local hors ventes sur plan mono-commune',
			'Calcul : 2026-09-10T17:08:41.721205Z',
			'Génération : 2026-09-10T17:09:00Z',
			'Population : dvf-canonical-windows-core.v1',
			'Source(s) : DVF',
			'Source : DVF',
			'Producteur : DGFiP / Etalab',
			'Licence : Licence Ouverte 2.0',
			'Référence : https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/',
			'Publication source : non renseignée',
			'Snapshot : non renseigné',
			'Import : non renseigné',
			'Couverture : unknown',
			'Période : 2025-01-01 → 2026-01-01 (fin exclue)',
			'995 mutations',
			'Statut inconnu',
			'Trace : rattachement cadastral non démontré',
			'Non-attribution : aucune identité logement prouvée',
			'Ne pas compter comme logements',
			'Limite : fenêtre descriptive seulement'
		].join(' · ');
		const source = model();
		source.grid.rows[4].cells.commune = cell([composite]);
		const original = structuredClone(source.grid.rows[4].cells.commune);

		const result = presentBienHierarchy(source);
		const screen = result.primary.rows.find((entry) => entry.concept.id.endsWith('dvf-maison-mediane-courante'))!.cells.commune;
		const printed = result.print.rows.find((entry) => entry.concept.id.endsWith('dvf-maison-mediane-courante:commune'))!.cells.scope;

		expect(screen.criticalSummary?.warnings).toEqual([
			'Population statistique : mutations résidentielles mono-local hors ventes sur plan mono-commune',
			'Couverture : unknown',
			'Période : 2025-01-01 → 2026-01-01 (fin exclue)',
			'995 mutations',
			'Statut inconnu',
			'Trace : rattachement cadastral non démontré',
			'Non-attribution : aucune identité logement prouvée',
			'Ne pas compter comme logements',
			'Limite : fenêtre descriptive seulement'
		]);
		expect(screen.reserves).toEqual([composite]);
		expect(printed.reserves).toEqual([composite]);
		expect(source.grid.rows[4].cells.commune).toEqual(original);
	});

	it('garde visible tout segment inconnu après une métadonnée repliée', () => {
		const source = model();
		const sourceWarning = 'Contrôle métier exceptionnel obligatoire';
		const populationWarning = 'Règle fournisseur nouvelle sans libellé connu';
		const qualifiedPopulation = 'Population : dvf-canonical-windows-core.v1 accompagnée d’une réserve nouvelle';
		source.grid.rows[4].cells.commune = cell([
			`Source : ADEME · ${sourceWarning}`,
			`Population : dvf-canonical-windows-core.v1 · ${populationWarning}`,
			qualifiedPopulation
		]);
		const original = structuredClone(source.grid.rows[4].cells.commune);

		const result = presentBienHierarchy(source);
		const screen = result.primary.rows.find((entry) => entry.concept.id.endsWith('dvf-maison-mediane-courante'))!.cells.commune;
		const printed = result.print.rows.find((entry) => entry.concept.id.endsWith('dvf-maison-mediane-courante:commune'))!.cells.scope;

		expect(screen.criticalSummary?.warnings).toEqual([sourceWarning, populationWarning, qualifiedPopulation]);
		expect(screen.reserves).toEqual(original.reserves);
		expect(printed.reserves).toEqual(original.reserves);
		expect(source.grid.rows[4].cells.commune).toEqual(original);
	});

	it('ne crée pas de résumé artificiel pour les absences vides', () => {
		const projected = presentBienHierarchy(model()).secondary.rows.find((entry) => entry.concept.id === 'secondary-empty-absence')!.cells.commune;
		expect(projected.kind).toBe('absent');
		expect(projected).not.toHaveProperty('criticalSummary');
	});

	it('force le disclosure quand les réserves ne contiennent que des métadonnées', () => {
		const source = model();
		source.grid.rows.push(row('metadata-only', cell(['Source : ADEME', 'Calcul : 2026-04'])));
		const projected = presentBienHierarchy(source).secondary.rows.find((entry) => entry.concept.id === 'metadata-only')!.cells.commune;

		expect(projected.criticalSummary).toEqual({ facts: [], warnings: [] });
		expect(projected.reserves).toEqual(['Source : ADEME', 'Calcul : 2026-04']);
	});

	it('garde une future ligne non communale inconnue dans la grille primaire', () => {
		const source = model();
		source.grid.rows.push({ concept: { id: 'future-dwelling-signal', label: 'Signal', definition: null, unit: null }, cells: {
			dwelling: cell(['Motif logement'])
		} });
		const result = presentBienHierarchy(source);

		expect(result.primary.rows.some((entry) => entry.concept.id === 'future-dwelling-signal')).toBe(true);
		expect(result.secondary.rows.some((entry) => entry.concept.id === 'future-dwelling-signal')).toBe(false);
	});

	it('partitionne les absences produites par le mapper communal réel dans le primaire', () => {
		const communal = mapBienCommuneContext({
			code_insee: '85047',
			indicateurs_dvf: { etat: 'not_qualified', motif: 'Version HTTP non qualifiée.' },
			indicateurs_dpe: { etat: 'suppressed', motif: 'Résultat non publié.' }
		} as CommuneSynthese)!;
		const source = model();
		const projected = presentBienHierarchy({
			...source,
			grid: { ...source.grid, rows: [...source.grid.rows.slice(0, 4), ...communal.rows] }
		});

		expect(projected.primary.rows.map((entry) => entry.concept.id)).toEqual(expect.arrayContaining([
			'bien-commune-context:dvf-indicateurs-absence',
			'bien-commune-context:dpe-indicateurs-absence'
		]));
	});

	it('aplatit l’impression sur une colonne sans les seuls placeholders structurels', () => {
		const source = model();
		const original = structuredClone(source);
		const result = presentBienHierarchy(source);

		expect(result.print.columns).toEqual([{
			id: 'scope', entity: 'bien', scope: 'bien', grain: 'Mailles explicites',
			label: 'Valeur, portée et réserves'
		}]);
		expect(result.print.rows.every((entry) => Object.keys(entry.cells).join() === 'scope')).toBe(true);
		expect(result.print.rows.some((entry) => entry.concept.id === 'dpe-attribution:dwelling')).toBe(false);
		const commune = result.print.rows.find((entry) => entry.concept.id === 'bien-commune-context:dvf-maison-mediane-courante:commune')!;
		expect(commune.concept.label).toBe('bien-commune-context:dvf-maison-mediane-courante — Commune');
		expect(commune.cells.scope.reserves).toEqual([
			...original.grid.rows[4].cells.commune.reserves,
			'Fait fournisseur', 'Réserve fournisseur'
		]);
		expect(commune.cells.scope).not.toHaveProperty('criticalSummary');
		expect(source).toEqual(original);
	});

	it('conserve chaque absence non applicable qualifiée par raison, réserve ou définition', () => {
		const source = model();
		const base = absent();
		source.grid.rows.push({ concept: { id: 'qualified-na', label: 'Absence qualifiée', definition: null, unit: null }, cells: {
			commune: { ...base, reason: { code: 'zero_denominator', label: 'Dénominateur nul', explanation: 'Aucun calcul possible.', evidence: '0 observation' } },
			context: { ...base, reserves: ['Motif source conservé'] },
			dwelling: { ...base, details: { ...base.details, definition: 'Définition publiée' } },
			qualified: { ...base, criticalSummary: { facts: ['Qualification fournisseur'], warnings: ['Alerte fournisseur', 'Qualification fournisseur'] } }
		} });
		const printed = presentBienHierarchy(source).print.rows.filter((entry) => entry.concept.id.startsWith('qualified-na:'));

		expect(printed.map((entry) => entry.concept.id).sort()).toEqual([
			'qualified-na:commune', 'qualified-na:context', 'qualified-na:dwelling', 'qualified-na:qualified'
		]);
		expect(printed.find((entry) => entry.concept.id.endsWith(':context'))!.cells.scope.reserves).toEqual(['Motif source conservé']);
		expect(printed.find((entry) => entry.concept.id.endsWith(':qualified'))!.cells.scope.reserves).toEqual([
			'Qualification fournisseur', 'Alerte fournisseur'
		]);
	});
});
