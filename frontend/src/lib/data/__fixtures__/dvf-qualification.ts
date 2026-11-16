import type { ReponseLogements } from '$lib/api/logements';
import type {
	DvfAttribution,
	DvfMutation,
	ResultatMultiResolution
} from '$lib/api/resolve';

/**
 * Corpus 100 % SYNTHÉTIQUE des cinq états affichables du bloc DVF (Phase 57, D-31/D-33).
 *
 * DOCTRINE — aucune adresse réelle, jamais. Les cas Reddit du 2026-08-19 (vente mixte, vente
 * d'ensemble, VEFA sans surface) sont reproduits par leur CONFIGURATION — nombre de locaux,
 * types, surfaces, nature de mutation, classe de qualification — et jamais par leur
 * localisation. Aucun nom de commune ni de voie réel n'entre ici : la voie est une « Voie
 * Synthétique », la commune une « Commune Test », les coordonnées sont l'îlot nul (0, 0), les
 * identifiants portent visiblement leur nature (`SYNTH-…`, section `ZZ`) et les codes INSEE
 * sont des placeholders hors référentiel (44999 / 85999). Même règle que `51-UAT-TEMOINS`, qui
 * n'est PAS versionné : le corpus réel reste privé, ce fichier est son doublure publiable.
 *
 * Les fixtures sont typées par les interfaces RÉELLES de `$lib/api/*`, jamais par des types
 * locaux : une dérive du contrat backend doit casser la compilation de ce module, pas se
 * découvrir à l'exécution d'un test qui continuerait de passer sur une forme périmée.
 *
 * MINIMISATION D-03 — le loader de `/bien/[token]` remplace `valeur_fonciere` et `prix_m2` par
 * une TRANCHE de prix quand la parcelle ne porte qu'UNE mutation ET qu'elle date de moins de
 * 24 mois. Toutes les mutations ci-dessous sont donc datées de 2021 (largement hors fenêtre),
 * et l'état `compatible_parcelle` porte en plus DEUX mutations dans `datasets.dvf` : c'est ce
 * qui permet au test d'asserter un PRIX au m², et non une tranche. Rapprocher une de ces dates
 * du présent ferait rougir le test d'attribution — c'est voulu, la minimisation prime.
 */

/**
 * Date de gel du corpus. Les dates de mutation lui sont TOUTES antérieures d'au moins deux ans
 * (cf. minimisation D-03 ci-dessus) et aucune n'est future — l'invariant DATA-05 tient donc
 * quelle que soit la date d'exécution du test.
 */
export const FROZEN_AT = '2026-08-25';

/**
 * Les états affichables du bloc DVF prouvés à l'écran (plan 57-16, D-33), plus
 * `type_bien_inconnu` ajouté par le plan 57-18 (D-41).
 *
 * `type_bien_inconnu` et `compatible_parcelle` portent DÉLIBÉRÉMENT le même historique de
 * parcelle : c'est tout l'objet du plan 57-18. Même donnée, deux décisions — selon que le type
 * du bien consulté a été DÉCLARÉ (`?type_bien=`) ou non. Sans déclaration, aucune compatibilité
 * ne peut être établie et aucun prix n'est publié ; avec elle, la transaction compatible de la
 * parcelle est publiée, et jamais présentée comme la vente de ce bien.
 */
export type DvfFixtureCategory =
	| 'compatible_parcelle'
	| 'type_bien_inconnu'
	| 'mixte'
	| 'ensemble'
	| 'vefa_sans_surface'
	| 'vente_sur_plan'
	| 'absence';

// --- Identité synthétique partagée -----------------------------------------------------------

/** Adresse inventée : ni voie, ni commune réelle. Sert de jeton de route et d'en-tête de fiche. */
export const ADRESSE_SYNTHETIQUE = '44 Voie Synthétique Alpha, 44000 Commune Test 44';
export const COMMUNE_SYNTHETIQUE = 'Commune Test 44';
export const CODE_POSTAL_SYNTHETIQUE = '44000';
export const CODE_INSEE_SYNTHETIQUE = '44999';
/** Parcelle manifestement synthétique : section `ZZ`, hors plan cadastral réel. */
export const ID_PARCELLE_SYNTHETIQUE = '44999000ZZ0051';

/** Version de règle publiée avec chaque décision (miroir de `DVF_ATTRIBUTION_RULE_VERSION`). */
const RULE_VERSION = '57-17/D-47';

// --- Mutations synthétiques ------------------------------------------------------------------

/**
 * Mutation complète au grain mutation. Chaque catégorie ne surcharge que la tranche qui la
 * distingue — classe, compteurs de locaux, surface, raison d'absence de prix au m².
 */
function baseMutation(overrides: Partial<DvfMutation> = {}): DvfMutation {
	return {
		id_mutation: 'SYNTH-DVF-0001',
		date_mutation: '2021-04-08',
		nature_mutation: 'Vente',
		valeur_fonciere: 186000,
		type_local: 'Appartement',
		surface_reelle_bati: 62,
		nombre_pieces_principales: 3,
		prix_m2: 3000,
		id_parcelle: ID_PARCELLE_SYNTHETIQUE,
		classe_mutation: 'residentiel_appartement',
		prix_m2_raison: null,
		mutation_mono_local: true,
		surface_bati_totale: 62,
		hors_stats_vefa: false,
		...overrides
	};
}

/** Le candidat compatible : appartement seul, vendu seul, surface et prix présents. */
const MUTATION_COMPATIBLE = baseMutation();

/**
 * L'antériorité de la parcelle. Sa seule fonction est de porter le compte à DEUX mutations,
 * ce qui sort la parcelle du régime d'isolement D-03 (cf. docstring du module).
 */
const MUTATION_ANTERIEURE = baseMutation({
	id_mutation: 'SYNTH-DVF-0002',
	date_mutation: '2021-01-15',
	valeur_fonciere: 171000,
	surface_reelle_bati: 57,
	surface_bati_totale: 57,
	nombre_pieces_principales: 2,
	prix_m2: 3000
});

/** Cas Reddit « vente mixte » : un logement ET un local d'activité, vendus à prix global. */
const MUTATION_MIXTE = baseMutation({
	id_mutation: 'SYNTH-DVF-0011',
	date_mutation: '2021-06-22',
	valeur_fonciere: 412000,
	type_local: null,
	surface_reelle_bati: 148,
	surface_bati_totale: 148,
	nombre_pieces_principales: 5,
	prix_m2: null,
	classe_mutation: 'mixte',
	prix_m2_raison: 'vente_mixte',
	mutation_mono_local: false
});

/** Cas Reddit « vente d'ensemble » : quatre logements vendus ensemble, prix global. */
const MUTATION_ENSEMBLE = baseMutation({
	id_mutation: 'SYNTH-DVF-0021',
	date_mutation: '2021-09-30',
	valeur_fonciere: 640000,
	type_local: null,
	surface_reelle_bati: 254,
	surface_bati_totale: 254,
	nombre_pieces_principales: 11,
	prix_m2: null,
	classe_mutation: 'ensemble_residentiel',
	prix_m2_raison: 'mutation_multi_locaux',
	mutation_mono_local: false
});

/**
 * Cas Reddit « VEFA sans surface » : achat sur plan, surface non fournie par la source, donc
 * aucun prix au m² calculable. `surface_bati_totale` est `null` — pas 0 : la source ne dit
 * rien, elle ne dit pas « zéro ».
 */
const MUTATION_VEFA = baseMutation({
	id_mutation: 'SYNTH-DVF-0031',
	date_mutation: '2021-11-04',
	nature_mutation: "Vente en l'état futur d'achèvement",
	valeur_fonciere: 249000,
	surface_reelle_bati: null,
	surface_bati_totale: null,
	nombre_pieces_principales: null,
	prix_m2: null,
	prix_m2_raison: 'surface_absente',
	hors_stats_vefa: true
});

/**
 * D-47 : la MÊME vente sur plan, mais la source fournit cette fois la surface. Le prix au m²
 * serait donc calculable — et c'est précisément pourquoi il ne doit pas être publié : il relève
 * du marché du neuf, non comparable à l'ancien. La fixture porte donc `prix_m2` renseigné, pour
 * que le test prouve un refus de publier, et non une incapacité à calculer.
 */
const MUTATION_VENTE_SUR_PLAN = baseMutation({
	id_mutation: 'SYNTH-DVF-0041',
	date_mutation: '2021-12-09',
	nature_mutation: "Vente en l'état futur d'achèvement",
	valeur_fonciere: 268000,
	surface_reelle_bati: 67,
	surface_bati_totale: 67,
	nombre_pieces_principales: 3,
	prix_m2: 4000,
	hors_stats_vefa: true
});

// --- Décisions d'attribution -----------------------------------------------------------------

/**
 * Les décisions, écrites dans le vocabulaire fermé du backend
 * (`app/dvf/schemas.py`) : `mutation` n'est portée QUE par `compatible_parcelle`, les états
 * mixte/ensemble/VEFA sont trois raisons distinctes d'un même statut `non_attribuable`, et
 * l'absence est un statut à part entière — jamais une panne, jamais un blanc.
 *
 * `preuve_locale` vaut `null` partout : aucune source de preuve d'identité locale n'existe en
 * Phase 57, donc aucun candidat n'est promu `exacte` (D-37).
 */
const ATTRIBUTIONS: Record<DvfFixtureCategory, DvfAttribution> = {
	// Le type a été DÉCLARÉ par le lecteur (`?type_bien=appartement`) : la provenance le dit EN
	// ENTIER — la source ET la valeur consultée — et `preuve_locale` reste `null` : une
	// déclaration n'est jamais une preuve d'identité du logement vendu (D-41).
	compatible_parcelle: {
		status: 'compatible_parcelle',
		reason: null,
		mutation: MUTATION_COMPATIBLE,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: [
			'parcelle_exacte',
			'type_compatible',
			'mono_local',
			'nature_vente',
			'surface_presente',
			'type_bien_fourni_utilisateur'
		],
		type_bien_source: 'utilisateur',
		type_bien_consulte: 'appartement',
		perimetre_ensemble: false,
		nb_locaux_residentiel: 1,
		nb_locaux_commercial: 0,
		nb_lots_source: 1
	},
	// MÊME historique que ci-dessus, décision opposée : sans type déclaré, la compatibilité ne
	// peut pas être établie. Aucune evidence, provenance `inconnu` — et surtout aucune mutation
	// portée, donc aucun prix au m² à l'écran.
	type_bien_inconnu: {
		status: 'non_attribuable',
		reason: 'type_bien_inconnu',
		mutation: null,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: [],
		type_bien_source: 'inconnu',
		type_bien_consulte: null,
		perimetre_ensemble: false,
		nb_locaux_residentiel: 1,
		nb_locaux_commercial: 0,
		nb_lots_source: 1
	},
	mixte: {
		status: 'non_attribuable',
		reason: 'derniere_vente_mixte',
		mutation: null,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: ['parcelle_exacte'],
		type_bien_consulte: null,
		perimetre_ensemble: true,
		nb_locaux_residentiel: 1,
		nb_locaux_commercial: 1,
		nb_lots_source: null
	},
	ensemble: {
		status: 'non_attribuable',
		reason: 'vente_ensemble',
		mutation: null,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: ['parcelle_exacte', 'type_compatible'],
		type_bien_consulte: null,
		perimetre_ensemble: true,
		nb_locaux_residentiel: 4,
		nb_locaux_commercial: 0,
		nb_lots_source: null
	},
	vefa_sans_surface: {
		status: 'non_attribuable',
		reason: 'vefa_sans_surface',
		mutation: null,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: ['parcelle_exacte', 'type_compatible', 'mono_local'],
		type_bien_consulte: null,
		perimetre_ensemble: false,
		nb_locaux_residentiel: 1,
		nb_locaux_commercial: 0,
		nb_lots_source: 1
	},
	// D-47 : vente sur plan AVEC surface. Toutes les preuves de compatibilité sont réunies, y
	// compris `surface_presente` — le refus tient au seul marché du neuf, et rien d'autre.
	vente_sur_plan: {
		status: 'non_attribuable',
		reason: 'vente_sur_plan',
		mutation: null,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: ['parcelle_exacte', 'type_compatible', 'mono_local', 'surface_presente'],
		type_bien_consulte: null,
		perimetre_ensemble: false,
		nb_locaux_residentiel: 1,
		nb_locaux_commercial: 0,
		nb_lots_source: 1
	},
	// `absente` ne décrit AUCUNE mutation : le backend lui interdit périmètre, compteurs et
	// preuves (validator `_valide_charge_utile` / `_valide_preuves`). La fixture respecte cet
	// interdit, sinon elle prouverait un écran que l'API ne peut pas produire.
	absence: {
		status: 'absente',
		reason: 'aucune_mutation',
		mutation: null,
		preuve_locale: null,
		rule_version: RULE_VERSION,
		evidence: [],
		type_bien_consulte: null
	}
};

/** L'historique de PARCELLE de chaque état — qualifié par la décision, jamais remplacé par elle. */
const HISTORIQUES: Record<DvfFixtureCategory, DvfMutation[]> = {
	// Deux mutations : hors régime d'isolement D-03, donc prix exact affichable (cf. module).
	compatible_parcelle: [MUTATION_COMPATIBLE, MUTATION_ANTERIEURE],
	// Le MÊME historique, à la ligne près : la différence de rendu est attribuable à la seule
	// déclaration du type, et à rien d'autre (D-41).
	type_bien_inconnu: [MUTATION_COMPATIBLE, MUTATION_ANTERIEURE],
	mixte: [MUTATION_MIXTE],
	ensemble: [MUTATION_ENSEMBLE],
	vefa_sans_surface: [MUTATION_VEFA],
	vente_sur_plan: [MUTATION_VENTE_SUR_PLAN],
	// Aucune vente enregistrée : la liste est VIDE, et c'est ce vide que la raison explique.
	absence: []
};

// --- Enveloppe de résolution -----------------------------------------------------------------

/** Bloc cadastre synthétique — la parcelle canonique que le loader relaie à /v1/bien/logements. */
function cadastrePayload() {
	return {
		id_parcelle: ID_PARCELLE_SYNTHETIQUE,
		commune: CODE_INSEE_SYNTHETIQUE,
		prefixe: '000',
		section: 'ZZ',
		numero: '0051',
		contenance: 410
	};
}

/** Bloc commune synthétique, réduit à ce que la Fiche Bien lit — aucune valeur d'un vrai INSEE. */
function communePayload() {
	return {
		code_insee: CODE_INSEE_SYNTHETIQUE,
		nom_commune: COMMUNE_SYNTHETIQUE,
		code_departement: '44',
		code_region: '52',
		population: 12000,
		nb_menages: 5200,
		nb_logements: 6100,
		densite: 210.5,
		superficie: 57.02,
		annee_demographie: 2021,
		revenu_median: 21500,
		annee_revenus: 2021,
		zone_abc: 'B2',
		zone_abc_reclassement: false,
		taux_vacance: 7.4,
		annee_vacance: 2023,
		taux_tfpb: 40.5,
		nom_epci: 'CC Synthétique',
		annee_fiscalite: 2024,
		loyer_m2_appartement: 9.5,
		loyer_m2_maison: 8.9,
		annee_loyers: 2023
	};
}

/**
 * Enveloppe `/v1/resolve` presque complète, dont chaque catégorie ne modifie qu'UNE tranche :
 * le bloc `datasets.dvf` (l'historique) et `dvf_attribution` (la décision). Tout le reste est
 * identique d'un état à l'autre — c'est ce qui fait que le test attribue la différence de
 * rendu à la décision, et à rien d'autre.
 */
export function baseResolution(
	overrides: Partial<ResultatMultiResolution> = {}
): ResultatMultiResolution {
	return {
		type_detecte: 'adresse',
		latitude: 0, // îlot nul assumé : jamais une coordonnée d'un bien réel
		longitude: 0,
		adresse: ADRESSE_SYNTHETIQUE,
		id_parcelle: ID_PARCELLE_SYNTHETIQUE,
		commune: COMMUNE_SYNTHETIQUE, // le NOM, pas le code INSEE (Pitfall 4)
		code_postal: CODE_POSTAL_SYNTHETIQUE,
		score: null,
		code_insee: CODE_INSEE_SYNTHETIQUE,
		ban_id: null,
		resolution: 'exact',
		parcelles_candidates: null,
		datasets: {
			cadastre: cadastrePayload(),
			dpe: [],
			dvf: [],
			commune: communePayload()
		},
		erreurs: [],
		avertissements: [],
		dataset_states: {
			cadastre: { dataset: 'cadastre', presence: 'value', absence_reason: null, raison: null },
			dpe: { dataset: 'dpe', presence: 'absent', absence_reason: 'not_reported', raison: null },
			dvf: { dataset: 'dvf', presence: 'value', absence_reason: null, raison: null },
			commune: { dataset: 'commune', presence: 'value', absence_reason: null, raison: null }
		},
		...overrides
	};
}

/**
 * L'enveloppe de résolution d'un état : historique + décision posés sur la base commune.
 *
 * `absence` est le seul état dont le bloc dataset passe en `absent` : sa liste est vide parce
 * qu'aucune vente n'est enregistrée, et le `dataset_state` doit dire la même chose que la
 * décision — deux surfaces qui se contrediraient seraient pires qu'une seule.
 */
export function resolutionPourEtat(categorie: DvfFixtureCategory): ResultatMultiResolution {
	const historique = HISTORIQUES[categorie];
	const base = baseResolution();
	return {
		...base,
		datasets: { ...base.datasets, dvf: historique },
		dvf_attribution: ATTRIBUTIONS[categorie],
		dataset_states: {
			...base.dataset_states,
			dvf:
				historique.length > 0
					? { dataset: 'dvf', presence: 'value', absence_reason: null, raison: null }
					: { dataset: 'dvf', presence: 'absent', absence_reason: 'not_reported', raison: null }
		}
	};
}

/** Enveloppe complète de `/v1/resolve` (statut + liste `donnees`), prête à être injectée. */
export function enveloppeResolvePourEtat(categorie: DvfFixtureCategory) {
	return { statut: 'ok', donnees: [resolutionPourEtat(categorie)], meta: { source: [] } };
}

// --- Enveloppe /v1/bien/logements -------------------------------------------------------------

/**
 * Réponse `/v1/bien/logements` SANS aucun signal positif : `mode: 'bien'`, donc la Fiche
 * classique est rendue et non l'interstitiel multi-logements. Le loader appelle cet endpoint
 * systématiquement dès qu'une parcelle canonique est résolue (D-15), et le garde-fou
 * fail-closed des tests avorte toute requête non interceptée — cette enveloppe est donc
 * OBLIGATOIRE pour chacun des cinq états, même si aucun n'assert sur elle.
 *
 * `ventes_immeuble` reste vide : les ventes de l'immeuble sont une surface de l'interstitiel
 * (D-17), pas de la Fiche, et les cinq états se prouvent sur le bloc DVF de la Fiche.
 */
export function enveloppeLogementsSansSignal(): ReponseLogements {
	return {
		statut: 'ok',
		donnees: {
			id_parcelle: ID_PARCELLE_SYNTHETIQUE,
			detection: {
				signaux: {
					copro_rnc: {
						positif: false,
						presence: 'absent',
						absence_reason: 'not_reported',
						compte: 0
					},
					dpe_appartement: {
						positif: false,
						presence: 'absent',
						absence_reason: 'not_reported',
						compte: 0
					},
					dvf_appartement: {
						positif: false,
						presence: 'absent',
						absence_reason: 'not_reported',
						compte: 0
					}
				},
				signaux_positifs: 0,
				mode: 'bien'
			},
			denominateur: { logements_connus: 0, lots_declares: null, non_distinguables: 0 },
			distribution_dpe: {},
				ventes_immeuble: [],
			diagnostics_charges: 0,
			tronque: false,
			dpe_attribution: { status: 'absent', reason: 'not_reported' }
		},
		meta: { source: null }
	};
}
