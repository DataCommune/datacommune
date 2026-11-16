import type { DpeAttributionClient, DpeInventoryPage } from '$lib/api/dpe-inventory';
import type { ResultatMultiResolution } from '$lib/api/resolve';
import type { FicheGrid, FicheGridCell, FicheGridColumn, FicheGridConcept, FicheGridDetails, FicheGridRow } from '$lib/types/fiche-grid';
import { formaterDistributionBatiment } from './dpe-attribution-view';
import { deriveDpeInventoryView } from './dpe-inventory-view';

export interface BienHierarchyModel { grid: FicheGrid; scopeNotes: readonly string[]; }

const NO_COMPARISON = 'Comparaison indisponible : unité, population, type, définition ou période compatibles non établis.';
const DWELLING_UNPROVEN = 'Aucun diagnostic n’est attribué au logement recherché.';
const ADDRESS_ASSOCIATION = 'Association déclarée par la source ; l’appartenance au logement reste à établir.';
const CONSULTED_ADDRESS_EXCLUDED = 'L’adresse consultée est exclue de ce contexte.';
const EMPTY_DETAILS: FicheGridDetails = { definition: null, ecart: null, tolerance: null, reference: null, raisonAbsence: null };

function details(definition: string | null, raisonAbsence: string | null = null): FicheGridDetails {
	return { ...EMPTY_DETAILS, definition, raisonAbsence };
}
function absentCell(grain: string, state: 'not_reported' | 'out_of_coverage' | 'temporary_error' | 'not_applicable', reserves: readonly string[], reason: string): FicheGridCell {
	const label = state === 'temporary_error' ? 'Indisponible temporairement' : state === 'not_applicable' ? '—' : 'Non disponible';
	return { kind: 'absent', state, label, explanation: reason, grain, unit: null, unitInline: false,
		estDatable: false, millesime: null, source: null, reserves, details: details(null, reason) };
}
function valueCell(display: string | number, grain: string, unit: string | null, reserves: readonly string[], measuredZero = false): FicheGridCell {
	return { kind: 'value', state: 'value', display, ...(measuredZero ? { measuredZero: true } : {}), grain, unit,
		unitInline: false, estDatable: true, millesime: null, source: { nom: 'ADEME', url: null }, reserves, details: EMPTY_DETAILS };
}
function contextCell(text: string, reserves: readonly string[]): FicheGridCell {
	return { kind: 'context', text, grain: 'Adresse', unit: null, unitInline: true, estDatable: false,
		millesime: null, source: { nom: 'RNB', url: null }, reserves, details: EMPTY_DETAILS };
}
function concept(id: string, label: string, definition: string | null, unit: string | null): FicheGridConcept {
	return { id, label, definition, unit };
}
function otherScope(grain: string, reason: string): FicheGridCell {
	return absentCell(grain, 'not_applicable', [], reason);
}

function attributionCell(attribution: DpeAttributionClient): FicheGridCell {
	switch (attribution.status) {
		case 'error': return absentCell('Logement', 'temporary_error', [DWELLING_UNPROVEN], 'L’attribution DPE est momentanément indisponible.');
		case 'absent': return absentCell('Logement', 'not_reported', [DWELLING_UNPROVEN], attribution.reason === 'identity_unavailable'
			? 'L’identité du logement ne permet pas d’établir une attribution DPE.' : 'Aucun DPE attribué au logement n’est publié par ce contrat.');
		case 'unassigned': return absentCell('Logement', 'not_reported', [DWELLING_UNPROVEN], 'L’identité du logement reste non prouvée.');
		case 'unavailable': return absentCell('Logement', 'not_reported', [DWELLING_UNPROVEN], 'Le contrat public d’attribution DPE n’est pas disponible.');
		default: return absentCell('Logement', 'not_reported', [DWELLING_UNPROVEN], 'Le statut reçu ne prouve aucune attribution au logement.');
	}
}

function inventoryCells(inventory: DpeInventoryPage | null) {
	if (inventory == null) {
		const reason = 'Inventaire documentaire non publié dans la réponse consultée.';
		return { count: absentCell('Adresse', 'not_reported', [ADDRESS_ASSOCIATION], reason),
			distribution: absentCell('Adresse', 'not_reported', [ADDRESS_ASSOCIATION], reason), notes: [reason] as readonly string[] };
	}
	const view = deriveDpeInventoryView(inventory);
	const reserves = [view.scopeNote, ...view.notes, view.importedAtLabel, view.sourceLabel];
	if (inventory.state === 'unavailable') {
		const reason = 'Les diagnostics sont momentanément indisponibles.';
		return { count: absentCell('Adresse', 'temporary_error', reserves, reason),
			distribution: absentCell('Adresse', 'temporary_error', reserves, reason), notes: reserves as readonly string[] };
	}
	if (inventory.state === 'identity_unavailable') {
		const reason = 'L’identité de l’adresse ne permet pas de constituer cet inventaire.';
		return { count: absentCell('Adresse', 'not_reported', reserves, reason),
			distribution: absentCell('Adresse', 'not_reported', reserves, reason), notes: reserves as readonly string[] };
	}
	if (inventory.state === 'absent' && inventory.total == null) {
		const reason = 'Aucun effectif documentaire n’est publié pour cette absence.';
		return { count: absentCell('Adresse', 'not_reported', reserves, reason),
			distribution: absentCell('Adresse', 'not_reported', reserves, reason), notes: reserves as readonly string[] };
	}
	const count = inventory.total == null
		? absentCell('Adresse', 'not_reported', reserves, 'Le nombre total de documents n’est pas publié.')
		: valueCell(inventory.total, 'Adresse', 'documents connus', reserves, inventory.total === 0);
	const displayed = view.distribution.filter((entry) => entry.count > 0).map((entry) => entry.label === 'Classes non disponibles'
		? `${entry.count} classes non disponibles` : `${entry.count} ${entry.label}`);
	const distribution = valueCell(displayed.length ? displayed.join(' · ') : 'Aucune classe publiée', 'Adresse', null, reserves);
	return { count, distribution, notes: reserves as readonly string[] };
}

function baseRows(inventory: DpeInventoryPage | null, attribution: DpeAttributionClient) {
	const projected = inventoryCells(inventory);
	return { notes: projected.notes, rows: [
		{ concept: concept('dpe-attribution', 'DPE du logement', 'Attribution au logement selon le contrat public DPE V2.', null), cells: {
			dwelling: attributionCell(attribution), context: otherScope('Adresse', 'Contexte documentaire'), commune: otherScope('Commune', 'Donnée communale') } },
		{ concept: concept('dpe-document-count', 'Diagnostics connus', 'Effectif documentaire associé à l’adresse par la source.', 'documents connus'), cells: {
			dwelling: otherScope('Logement', 'Contexte documentaire'), context: projected.count, commune: otherScope('Commune', 'Donnée communale') } },
		{ concept: concept('dpe-distribution', 'Répartition des diagnostics', 'Comptes documentaires par classe DPE, inconnues incluses.', null), cells: {
			dwelling: otherScope('Logement', 'Contexte documentaire'), context: projected.distribution, commune: otherScope('Commune', 'Donnée communale') } }
	] satisfies FicheGridRow[] };
}

function rnbRow(attribution: DpeAttributionClient): FicheGridRow | null {
	if (attribution.version !== 2) return null;
	if (attribution.status !== 'unassigned' && attribution.status !== 'absent') return null;
	const context = attribution.building_context;
	if (context == null) return null;
	const countNote = attribution.graph_state === 'incoherent'
		? `${context.count} lignes documentaires dans ce contexte ; le graphe incohérent ne permet pas de les qualifier de terminales.`
		: `${context.count} documents terminaux dans ce contexte.`;
	const distribution = formaterDistributionBatiment(context.distribution);
	const reserves = [countNote, CONSULTED_ADDRESS_EXCLUDED,
		'Répartition A–G incomplète : les classes inconnues ne sont pas publiées explicitement.',
		...(context.truncated ? ['Contexte tronqué : les limites ou le graphe documentaire sont incomplets.'] : [])];
	return { concept: concept('dpe-rnb-context', 'Contexte documentaire RNB', 'Autres associations BAN reliées via le RNB ; contexte indicatif sans identité de bâtiment.', null), cells: {
		dwelling: otherScope('Logement', 'Contexte documentaire'), context: contextCell([
			'Autres adresses liées via le RNB — contexte indicatif', distribution
		].filter((part): part is string => part != null).join(' · '), reserves),
		commune: otherScope('Commune', 'Donnée communale') } };
}

function communalRows(communal: FicheGrid | null): FicheGridRow[] {
	if (communal == null) return [];
	const sourceColumn = communal.columns.find((column) => column.entity === 'commune');
	if (sourceColumn == null) return [];
	return communal.rows.flatMap((row) => {
		const sourceCell = row.cells[sourceColumn.id];
		if (sourceCell == null) return [];
		return [{ concept: { ...row.concept }, cells: {
			dwelling: absentCell('Logement', 'not_applicable', [], 'Donnée communale — comparaison indisponible.'),
			context: absentCell('Adresse', 'not_applicable', [], 'Donnée communale — comparaison indisponible.'), commune: sourceCell } }];
	});
}

function columns(multi: ResultatMultiResolution, communal: FicheGrid | null): FicheGridColumn[] {
	const sourceCommune = communal?.columns.find((column) => column.entity === 'commune');
	return [
		{ id: 'dwelling', entity: 'bien', scope: 'bien', grain: 'Logement', label: 'Logement recherché' },
		{ id: 'context', entity: 'adresse', scope: 'bien', grain: 'Adresse', label: multi.adresse ?? 'Adresse non disponible' },
		{ id: 'commune', entity: 'commune', scope: 'commune', grain: 'Commune',
			label: sourceCommune?.label ?? multi.commune ?? multi.code_insee ?? 'Commune non renseignée',
			...(sourceCommune?.code ?? multi.code_insee ? { code: sourceCommune?.code ?? multi.code_insee ?? undefined } : {}) }
	];
}

/** Pure projection: document consultation is deliberately absent from the inputs. */
export function mapBienHierarchy(multi: ResultatMultiResolution, inventory: DpeInventoryPage | null,
	attribution: DpeAttributionClient, communal: FicheGrid | null): BienHierarchyModel {
	const base = baseRows(inventory, attribution);
	const context = rnbRow(attribution);
	return { grid: { id: 'bien-hierarchy', columns: columns(multi, communal),
		rows: [...base.rows, ...(context ? [context] : []), ...communalRows(communal)] },
		scopeNotes: [DWELLING_UNPROVEN, ADDRESS_ASSOCIATION, NO_COMPARISON,
			'Aucun bâtiment unique n’est prouvé par ce contexte RNB.',
			'Les autres clés BAN peuvent désigner des adresses différentes de l’adresse consultée.', ...base.notes,
			...(context ? context.cells.context.reserves : [])] };
}
