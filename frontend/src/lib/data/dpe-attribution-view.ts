import {
	projectDpeAttributionForClient,
	type DpeAttributionClient,
	type DpeBuildingContextV2,
	type DpeInventoryGraphState
} from '$lib/api/dpe-inventory';
import type {
	DpeCandidate,
	DpePublicDiagnostic,
	TypeBienDeclare
} from '$lib/api/resolve';

const ABSENCE_TEXTE = 'Aucun DPE rattaché exactement à cette adresse';
const IDENTITE_TEXTE = 'L’identité du logement ne permet pas d’établir une attribution DPE.';
const BATIMENT_TITRE =
	'Autres adresses liées via le RNB — contexte indicatif';
const ERREUR_CONSTAT = 'Données DPE momentanément indisponibles';
const ERREUR_CONDUITE =
	'Réessayez plus tard ; les autres informations de la fiche restent disponibles.';
const CONTRAT_CONSTAT = 'Contrat DPE à actualiser';
const CONTRAT_CONDUITE = 'Actualisez la fiche ; les autres informations restent disponibles.';
const CLASSES_DPE = ['A', 'B', 'C', 'D', 'E', 'F', 'G'] as const;

export interface DpeFicheSelection {
	diagnostic: DpePublicDiagnostic | null;
	gapState: 'out_of_coverage' | 'temporary_error' | null;
}

/** Legacy anchor state kept until the route migration. V2 never produces a valid anchor. */
export type DpeAncre = 'aucune' | 'valide' | 'invalide';

export interface DpeBatimentModel {
	titre: string;
	effectif: string;
	distribution: string | null;
	tronque: boolean;
}

export interface DpeAttributionView {
	status: DpeAttributionClient['status'];
	reason?: DpeAttributionClient['reason'];
	building_context?: DpeBuildingContextV2 | null;
	graph_state?: DpeInventoryGraphState;
	ficheSelection: DpeFicheSelection;
	precisionLabel: string | null;
	precisionTexte: string | null;
	perimeLabel: string | null;
	absenceTexte: string | null;
	erreurConstat: string | null;
	erreurConduite: string | null;
	batiment: DpeBatimentModel | null;
	choixPrecision: string | null;
	choixHint: string | null;
	candidats: DpeCandidate[];
	candidatChoisi: DpeCandidate | null;
	ancre: DpeAncre;
	ancreInvalideTexte: string | null;
	retourHref: string | null;
	retourLabel: string | null;
}

export interface OptionsDpeAttributionView {
	hrefBase: string;
	typeBien?: TypeBienDeclare;
	/** Kept for old shared URLs; it is intentionally ignored by the V2 mapper. */
	dpe?: string | null;
	/** Changes only page granularity. It never selects an individual document. */
	immeuble?: boolean;
}

export function attributionInattendue(attribution: never): never {
	const statut = (attribution as { status?: unknown } | null)?.status;
	throw new Error(
		`deriveDpeAttributionView: statut d'attribution DPE non traité — ${String(statut)}`
	);
}

export function formaterDistributionBatiment(distribution: Record<string, number>): string | null {
	const presentes = CLASSES_DPE.filter((classe) => (distribution[classe] ?? 0) > 0).map(
		(classe) => `${distribution[classe]} ${classe}`
	);
	return presentes.length === 0
		? null
		: `Répartition des diagnostics connus : ${presentes.join(' · ')}`;
}

function deriveBatiment(contexte: DpeBuildingContextV2): DpeBatimentModel {
	return {
		titre: BATIMENT_TITRE,
		effectif:
			contexte.count === 1
				? '1 diagnostic connu sur les autres adresses liées'
				: `${contexte.count} diagnostics connus sur les autres adresses liées`,
		distribution: formaterDistributionBatiment(contexte.distribution),
		tronque: contexte.truncated
	};
}

function neutralView(attribution: DpeAttributionClient): DpeAttributionView {
	const hasContext =
		(attribution.status === 'unassigned' || attribution.status === 'absent') &&
		attribution.building_context != null;
	return {
		status: attribution.status,
		reason: attribution.reason,
		building_context: hasContext ? attribution.building_context : null,
		graph_state: attribution.graph_state,
		ficheSelection: { diagnostic: null, gapState: 'out_of_coverage' },
		precisionLabel: null,
		precisionTexte: null,
		perimeLabel: null,
		absenceTexte: null,
		erreurConstat: null,
		erreurConduite: null,
		batiment: hasContext ? deriveBatiment(attribution.building_context) : null,
		choixPrecision: null,
		choixHint: null,
		candidats: [],
		candidatChoisi: null,
		ancre: 'aucune',
		ancreInvalideTexte: null,
		retourHref: null,
		retourLabel: null
	};
}

/**
 * Convert the closed V2 decision to presentation data. Legacy exact/multiple/building objects,
 * unknown versions and malformed V2 objects all become a neutral refresh state. The former
 * query anchor, rank, floor and candidates are never read, so this function cannot populate
 * ficheSelection.
 */
export function deriveDpeAttributionView(
	attribution: unknown,
	_opts: OptionsDpeAttributionView
): DpeAttributionView {
	const projected = projectDpeAttributionForClient(attribution);
	const view = neutralView(projected);

	switch (projected.status) {
		case 'unassigned':
			return view;
		case 'absent':
			return {
				...view,
				absenceTexte: projected.reason === 'identity_unavailable' ? IDENTITE_TEXTE : ABSENCE_TEXTE
			};
		case 'error':
			return {
				...view,
				ficheSelection: { diagnostic: null, gapState: 'temporary_error' },
				erreurConstat: ERREUR_CONSTAT,
				erreurConduite: ERREUR_CONDUITE
			};
		case 'unavailable':
			return {
				...view,
				ficheSelection: { diagnostic: null, gapState: 'temporary_error' },
				erreurConstat: CONTRAT_CONSTAT,
				erreurConduite: CONTRAT_CONDUITE
			};
	}

	return attributionInattendue(projected);
}
