// Modèle de présentation de l'attribution DVF — dérivation pure et EXHAUSTIVE (Phase 57, D-16/D-37).
//
// Ce module est la seule frontière entre la décision fermée du backend et ce que la Fiche affiche
// à propos des ventes : le KPI de prix, la mention qui l'accompagne, et les étiquettes de
// l'historique de parcelle. Il est écrit sur le patron EXACT de `dpe-attribution-view.ts`, pour
// la même raison : il n'existe qu'UNE règle d'attribution, écrite et testée en Python, et une
// seule mise en forme, écrite et testée ici.
//
// LA SUBSTITUTION QUE CE MODULE RETIRE : le KPI « Prix au m² (dernière vente) » lisait la
// PREMIÈRE LIGNE du bloc DVF de la charge utile, c'est-à-dire la transaction la plus récente de la
// PARCELLE, et la présentait comme la dernière vente DU BIEN. Sur une parcelle partagée, le prix
// affiché pouvait être celui d'un local commercial du rez-de-chaussée, d'un immeuble entier vendu
// à prix global, ou d'un autre logement. L'état ne se lit donc plus sur le remplissage d'une
// liste : il se lit sur `dvf_attribution.status`, et rien d'autre ne peut le produire.
//
// Trois interdits structurels, comme pour le DPE :
//   - aucune lecture de la charge utile brute, aucune coordonnée, aucun identifiant de parcelle :
//     la seule entrée de la dérivation d'état est `DvfAttribution` ;
//   - aucun recalcul de la compatibilité, du type de bien ou du périmètre d'une mutation : ce
//     qui est reçu est MIS EN FORME, jamais re-décidé ;
//   - aucun état intermédiaire : les cinq statuts backend sont traités par un `switch` sans
//     branche `default`, et l'assertion `never` exportée plus bas rend toute future variante
//     visible à la compilation plutôt qu'au support.
//
// `exacte` est traité par le `switch` bien qu'aucune source de preuve locale n'existe à ce jour
// (le backend ne le produit JAMAIS, D-37) : l'exhaustivité est une propriété du module, pas une
// observation sur les données du moment.
//
// COPIE ARBITRÉE (D-19) : toutes les chaînes affichables vivent dans la SEULE constante
// `COPIE_DVF` ci-dessous. Elles ne sont plus une proposition : le dossier `57-COPIE-DVF.md` a été
// tranché mot à mot le 2026-08-25 (« Arbitrage rendu le 2026-08-25 par Ianis »), et le plan 57-15
// l'a appliqué mécaniquement — une clé du dossier = une clé de cette constante. Aucune chaîne ne
// doit être dispersée dans le corps des fonctions, et toute modification ultérieure d'un de ces
// mots exige un NOUVEL arbitrage D-19, jamais une retouche au fil d'un refactor.

import type { DvfAttribution, DvfAttributionStatus, DvfMutation } from '$lib/api/resolve';

// --- Copie arbitrée (D-19, dossier 57-COPIE-DVF.md, tranché le 2026-08-25) --------------------

/**
 * L'INTÉGRALITÉ de la copie DVF de la Fiche Bien et de la Fiche Commune. Chaque clé correspond à
 * une ligne du dossier d'arbitrage `57-COPIE-DVF.md`, tranché le 2026-08-25 (D-19), pour que la
 * copie affichée soit vérifiable ligne à ligne contre ce dossier — `copie-dvf.test.ts` le lit sur
 * le disque et le prouve. Modifier un de ces mots exige un nouvel arbitrage.
 */
export const COPIE_DVF = {
	/**
	 * Libellé du KPI. L'ancien — « Prix au m² (dernière vente) » — AFFIRMAIT une attribution :
	 * « dernière vente » sous-entend « de ce bien ». Le nouveau nomme ce qui est réellement
	 * observé, une transaction de la parcelle, et laisse la mention dire ce qui n'est pas prouvé.
	 * D-46 (2026-08-25 soir) : forme courte, sans tiret cadratin.
	 */
	kpiLabel: 'Prix au m² sur la parcelle',

	/** Mentions d'état du KPI : `mention` est la ligne visible, `detail` la phrase du panneau ℹ️. */
	etats: {
		exacte: {
			mention: 'Vente rattachée à ce bien',
			detail:
				"Cette vente est rattachée à ce bien par une preuve d'identité du logement vendu."
		},
		compatible_parcelle: {
			mention: 'Non attribuée',
			detail:
				'Transaction compatible observée sur cette parcelle, non attribuée au bien. Sans ' +
				"preuve d'identité du logement vendu, ce prix reste celui d'une transaction de la " +
				'parcelle, jamais le prix certain de ce logement.'
		}
	},

	/**
	 * Une ligne par raison publique du vocabulaire fermé `DVF_ATTRIBUTION_RAISONS` (13 clés).
	 * `mention` doit tenir sur une ligne : c'est ce que lit quelqu'un dont le bien n'a pas de
	 * vente attribuable.
	 */
	raisons: {
		aucune_mutation: {
			mention: 'Aucune vente enregistrée sur cette parcelle',
			detail:
				"Aucune vente n'est enregistrée sur cette parcelle dans le fichier public des ventes."
		},
		derniere_vente_mixte: {
			mention: "Vente mêlant logement et local d'activité",
			detail:
				"La transaction la plus récente mêle un logement et un local d'activité, vendus à prix " +
				'global : ce prix ne peut pas être rapporté à ce bien.'
		},
		derniere_vente_commerciale: {
			mention: "Vente de locaux d'activité",
			detail:
				"La transaction la plus récente ne porte que des locaux d'activité : elle ne concerne " +
				'pas ce logement.'
		},
		vente_ensemble: {
			mention: 'Plusieurs logements vendus ensemble',
			detail:
				'La transaction la plus récente porte plusieurs logements vendus ensemble, à prix ' +
				"global : ce prix n'est celui d'aucun logement pris séparément."
		},
		type_incompatible: {
			mention: "Vente d'un autre type de bien",
			detail:
				'La transaction la plus récente porte un type de bien différent de celui consulté : ' +
				"une vente de maison n'est jamais rapportée à un appartement, ni l'inverse."
		},
		classe_non_residentielle: {
			mention: 'Vente hors logement unique',
			detail:
				"La transaction la plus récente n'est pas la vente d'un logement unique : aucun prix " +
				'ne lui est rapporté.'
		},
		vefa_sans_surface: {
			mention: 'Vente sur plan, surface non fournie',
			detail:
				"Vente en l'état futur d'achèvement (achat sur plan) : la surface n'est pas fournie " +
				"par le fichier des ventes, aucun prix au mètre carré n'est calculable."
		},
		// D-47 : la vente sur plan AVEC surface. Le prix au m² serait calculable, mais il relève du
		// marché du neuf — le publier comme prix de ce logement ancien serait la comparaison que
		// D-18 refuse. Sans surface, la raison reste `vefa_sans_surface`, au-dessus.
		vente_sur_plan: {
			mention: 'Vente sur plan',
			detail:
				"Vente en l'état futur d'achèvement (achat sur plan) : marché du neuf, non " +
				"comparable à l'ancien, aucun prix au m² retenu."
		},
		nature_hors_vente: {
			mention: "Transaction qui n'est pas une vente",
			detail:
				"La transaction la plus récente n'est pas une vente au sens du périmètre publié " +
				'(adjudication, échange, expropriation).'
		},
		type_bien_inconnu: {
			mention: 'Type du bien non déterminé',
			detail:
				"Le type du bien consulté n'est pas déterminé : aucune compatibilité ne peut être " +
				"établie avec les transactions de la parcelle, et aucune n'est retenue au hasard."
		},
		parcelle_indisponible: {
			mention: 'Aucune parcelle cadastrale résolue',
			detail:
				"Aucune parcelle cadastrale n'a pu être résolue pour cette adresse : aucune vente n'a " +
				'été cherchée.'
		},
		entree_illisible: {
			mention: 'Données de vente indisponibles',
			detail:
				"Les données de vente n'ont pas pu être lues. Cette indisponibilité est technique : " +
				"elle ne signifie pas qu'aucune vente n'a eu lieu ici."
		},
		temporary_error: {
			mention: 'Données de vente momentanément indisponibles',
			detail:
				'Les données de vente sont momentanément indisponibles. Réessayez plus tard ; les ' +
				'autres informations de la fiche restent disponibles.'
		}
	},

	/**
	 * Une étiquette par valeur du vocabulaire fermé `CLASSES_MUTATION` (7 valeurs), telles
	 * qu'elles apparaissent dans l'historique de parcelle. `inconnue` couvre l'absence de
	 * qualification : elle n'est pas une 8e classe, c'est le refus d'en inventer une.
	 */
	classes: {
		residentiel_maison: "Vente d'une maison seule",
		residentiel_appartement: "Vente d'un appartement seul",
		ensemble_residentiel: 'Vente de plusieurs logements ensemble, à prix global',
		mixte: "Vente mêlant logement et local d'activité, à prix global",
		commerciale: "Vente de locaux d'activité",
		terrain_seul: 'Vente de terrain sans bâti',
		hors_perimetre: 'Transaction hors du périmètre publié',
		inconnue: 'Nature de la transaction non qualifiée'
	},

	/**
	 * Suffixe VEFA — accolé à l'étiquette de classe ENTRE PARENTHÈSES, jamais rendu seul
	 * (D-12/D-18). D-46 (2026-08-25 soir) : forme courte, le tiret cadratin de séparation devient
	 * la parenthèse, et l'acronyme développé sort du suffixe pour ne pas imbriquer deux paires.
	 */
	suffixeVefa: 'vente sur plan',

	/** Libellés des lignes « sur ce bien » du thème Marché & prix. */
	lignesBien: {
		classe: 'Nature de la transaction',
		valeurFonciere: 'Valeur foncière de la transaction',
		prixM2: 'Prix au m²',
		date: 'Date de mutation',
		typeLocal: 'Type de local',
		surfaceBatiTotale: 'Surface bâtie de la transaction',
		surfaceReelle: 'Surface réelle bâtie',
		pieces: 'Nombre de pièces',
		historique: 'Autre transaction sur la parcelle',
		gapPrix: 'Prix médian au m²',
		gapDate: 'Dernière mutation'
	},

	/**
	 * Libellés des lignes DVF de la Fiche Commune. La mention d'exclusion (D-18) est portée par le
	 * libellé lui-même : la structure actuelle n'offre pas d'emplacement de réserve, et D-21
	 * interdit d'en créer un. L'arbitrage du 2026-08-25 a écarté l'acronyme du libellé visible au
	 * profit de « hors ventes sur plan » ; VEFA n'est développé que dans l'info-bulle de chaque
	 * ligne (`explications.ts`), où l'espace le permet.
	 */
	lignesCommune: {
		prixMedian: 'Prix médian de vente (hors ventes sur plan)',
		prixMedianMaison: 'Prix médian maison (hors ventes sur plan)',
		ventesMaison: 'Ventes de maisons sur 12 mois',
		prixMedianAppartement: 'Prix médian appartement (hors ventes sur plan)',
		ventesAppartement: "Ventes d'appartements sur 12 mois",
		mutations12m: 'Mutations sur 12 mois'
	}
} as const;

// --- Types de sortie -------------------------------------------------------------------------

/**
 * Tout ce que la surface DVF de la Fiche a le droit d'afficher, déjà décidé (aucune décision
 * dans le DOM, aucune seconde règle en aval).
 *
 * `mutation` non nul veut dire « une transaction peut être montrée » : `exacte`, ou
 * `compatible_parcelle`. Partout ailleurs il vaut `null` et `gapState` dit POURQUOI — c'est
 * cette distinction qui interdit à une panne de se lire comme « aucune vente ici » (D-16).
 */
export interface DvfAttributionView {
	status: DvfAttributionStatus;
	mutation: DvfMutation | null;
	gapState: 'out_of_coverage' | 'temporary_error' | null;
	/** Libellé neutre du KPI — il n'affirme aucune attribution, quel que soit l'état. */
	kpiLabel: string;
	/** LA ligne visible sur le KPI : l'état retenu, ou l'absence et sa raison, en une ligne. */
	mention: string | null;
	/** La phrase complète, rendue par le panneau ℹ️ du KPI. */
	mentionDetaillee: string | null;
	/** Vrai quand la mutation examinée porte plusieurs locaux : son prix est GLOBAL (DVF-03). */
	perimetreEnsemble: boolean;
}

/**
 * Assertion d'exhaustivité. Elle n'est pas décorative : le jour où le backend publiera un sixième
 * statut, ce module cessera de compiler au lieu de rendre silencieusement un prix sans état. Elle
 * échoue aussi bruyamment à l'exécution, pour le cas d'un payload non typé.
 */
export function attributionDvfInattendue(attribution: never): never {
	const statut = (attribution as { status?: unknown } | null)?.status;
	throw new Error(
		`deriveDvfAttributionView: statut d'attribution DVF non traité — ${String(statut)}`
	);
}

// --- Formateurs ------------------------------------------------------------------------------

/**
 * Étiquette lisible d'une mutation de l'historique de parcelle (D-17). Une classe absente ou
 * inconnue du vocabulaire fermé ne devient JAMAIS une classe voisine : elle est nommée non
 * qualifiée. Une vente sur plan porte son suffixe en plus de sa classe — c'est un marché
 * différent, et le taire est exactement ce que D-12/D-18 refusent.
 */
export function etiquetteMutation(classe: string | null, vefa: boolean = false): string {
	const classes: Record<string, string> = COPIE_DVF.classes;
	const etiquette = (classe != null && classes[classe]) || COPIE_DVF.classes.inconnue;
	return vefa ? `${etiquette} (${COPIE_DVF.suffixeVefa})` : etiquette;
}

/**
 * Vue neutre d'un statut : rien d'affiché, aucune mutation, `gapState` non renseigné. Chaque
 * branche du `switch` part de là et n'active QUE ce que son état autorise — un champ oublié est
 * donc silencieux plutôt que faux, et jamais `undefined`.
 */
function vueNeutre(status: DvfAttributionStatus): DvfAttributionView {
	return {
		status,
		mutation: null,
		gapState: null,
		kpiLabel: COPIE_DVF.kpiLabel,
		mention: null,
		mentionDetaillee: null,
		perimetreEnsemble: false
	};
}

/** Copie d'une raison fermée. Une raison hors vocabulaire ne rend RIEN plutôt qu'un mot inventé. */
function copieRaison(raison: string): { mention: string; detail: string } | null {
	const raisons: Record<string, { mention: string; detail: string }> =
		COPIE_DVF.raisons;
	return raisons[raison] ?? null;
}

// --- Dérivation ------------------------------------------------------------------------------

/**
 * Transforme la décision backend en modèle de vue.
 *
 * `null`/`undefined` — une enveloppe qui ne porte PAS de décision — est traité comme une
 * indisponibilité technique, jamais comme une absence de vente : le champ est additif et
 * optionnel, donc son absence prouve seulement que la décision n'a pas été calculée. C'est la
 * doctrine D-14 de la Phase 51, appliquée telle quelle. Le repli est FERMÉ : un appelant qui
 * oublie de passer la décision affiche `n.d.` et la raison, il ne retombe jamais sur une lecture
 * de la liste de mutations.
 */
export function deriveDvfAttributionView(
	attribution: DvfAttribution | null | undefined
): DvfAttributionView {
	if (attribution == null) {
		const copie = COPIE_DVF.raisons.temporary_error;
		return {
			...vueNeutre('error'),
			gapState: 'temporary_error',
			mention: copie.mention,
			mentionDetaillee: copie.detail
		};
	}

	const neutre = vueNeutre(attribution.status);
	const perimetreEnsemble = attribution.perimetre_ensemble === true;

	switch (attribution.status) {
		case 'exacte':
			return {
				...neutre,
				mutation: attribution.mutation,
				mention: COPIE_DVF.etats.exacte.mention,
				mentionDetaillee: COPIE_DVF.etats.exacte.detail,
				perimetreEnsemble
			};

		case 'compatible_parcelle':
			// D-37 : le prix EST publié, et la mention dit dans le même mouvement qu'il n'est pas
			// attribué au bien. Les deux voyagent ensemble ou pas du tout.
			return {
				...neutre,
				mutation: attribution.mutation,
				mention: COPIE_DVF.etats.compatible_parcelle.mention,
				mentionDetaillee: COPIE_DVF.etats.compatible_parcelle.detail,
				perimetreEnsemble
			};

		case 'non_attribuable':
		case 'absente': {
			// Une mutation existe (ou non) mais rien n'est attribuable : l'écart est de couverture,
			// pas technique. La raison est ce qui rend l'absence explicable en une ligne (D-16).
			const copie = copieRaison(attribution.reason);
			return {
				...neutre,
				gapState: 'out_of_coverage',
				mention: copie?.mention ?? null,
				mentionDetaillee: copie?.detail ?? null,
				perimetreEnsemble
			};
		}

		case 'error': {
			// La seule valeur qui distingue une panne d'une absence métier dans tout l'aval.
			const copie = copieRaison(attribution.reason);
			return {
				...neutre,
				gapState: 'temporary_error',
				mention: copie?.mention ?? null,
				mentionDetaillee: copie?.detail ?? null
			};
		}
	}

	// Aucune branche `default` : `attribution` est narrowée à `never` ici, donc un sixième statut
	// casse la compilation au lieu de tomber dans un fourre-tout silencieux.
	return attributionDvfInattendue(attribution);
}
