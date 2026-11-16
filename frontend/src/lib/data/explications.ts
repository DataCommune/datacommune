// Plain-language explanation dictionary for every interpretable Fiche metric (Phase 49, UI-07,
// D-13/D-15/D-16). Pure module — zero Svelte imports, zero network access, LEAF module: the
// mappers and the ℹ️ component import this, never the reverse.
//
// WHY THIS MODULE EXISTS: the Fiche shows figures whose meaning is not obvious to a reader
// ("revenu médian" is NOT a household's gross income, "raccordable à la fibre" is not "abonné").
// The registry `fiche-fields.v1.json` already carries a `description` per field, but those texts
// are CONTRACT-grade (they document the SQL/ETL semantics), not reader-facing. This dictionary is
// the reader-facing layer, keyed by the SAME `field_id` key space — never ad hoc keys.
//
// D-13 — WHAT LIVES HERE AND WHAT DOES NOT:
//   HERE   : definition + unit spelled out, static, versioned with the code.
//   NOT HERE: the millésime and the source. Those are read LIVE from the API response
//             (`DatasetProvenance` on /v1/resolve, `annee_*` + `meta.source` on /v1/commune) by
//             the helpers at the bottom of this file. A millésime frozen in a definition would
//             become false at the first ETL refresh, silently.
//   NOT HERE EITHER: the reference value and the numeric gap — those live in `referentiels.ts`
//             (D-03), so a published figure is stated in exactly one place.
//
// D-16 — FAIL-CLOSED BY CONSTRUCTION: `EXPLICATIONS` is declared
// `satisfies Record<CoveredFieldId, ExplicationEntry>`, so a covered field_id without an entry is
// a COMPILATION error, not a runtime blank. `explications.test.ts` closes the other half of the
// loop: COVERED ∪ EXCLUDED must equal `listFieldIds()` exactly, so adding a field to the registry
// without deciding its ℹ️ fate turns the suite red.
//
// D-15 — the exclusion list below is LOCKED (49-UI-SPEC §Phase 49 ℹ️ Coverage Contract). Anything
// else in the registry is covered. Nothing may be excluded "because writing its definition is
// hard": an exclusion is a decision, taken once, in this list.
//
// D-12 — acronyms are expanded on FIRST USE INSIDE EACH ENTRY, not once for the whole file: a
// popover is read standalone, out of any reading order, so an expansion made in a sibling entry
// would never reach the reader of this one.
//
// Pitfall 2 — this is a NEW, SEPARATE file: `fiche-fields.v1.json` must NOT gain keys. Its own
// descriptor forbids it ("aucune cle de provenance/fraicheur ... ne doit apparaitre ici") and it
// is kept byte-identical across 3 locations by `api/backend/scripts/sync_semantic_contract.py`.

import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import type { DatasetProvenance } from '$lib/api/resolve';
import type { Scope } from '$lib/types/fiche';

/** One reader-facing explanation. Deliberately a single field: see D-13 note above. */
export interface ExplicationEntry {
	/** 1-2 plain-language French sentences. Unit spelled out, acronyms expanded. */
	definition: string;
}

/**
 * D-15 locked exclusions — every registry field_id that gets NO ℹ️ entry, with its reason.
 * Three families, deliberately kept in one list so the test can assert a clean partition:
 *
 *  1. Pure identifiers — a code or a name explains itself; an ℹ️ would be noise.
 *  2. Structural-gap placeholders (`allowed_states: ["out_of_coverage"]` only) — they are not
 *     metrics at all. When they render, they use the D-10 state glossary absence copy, which
 *     already says why the value is missing.
 *  3. Millésime carriers (`*.annee`) — D-18 architecture call: a vintage is folded into the
 *     millésime SLOT of its sibling metrics' popover ("Millésime : {revenus.annee}"), it is not
 *     itself a metric to explain. Excluding them here is what makes that slot the only place a
 *     vintage is shown.
 * A fourth family, « served but not displayed yet », held the per-m² values of a DPE document
 * (04.1-12, C51) until plan 04.5-12 displayed them; it is empty since and covered below.
 */
export const EXCLUDED_FIELD_IDS = [
	// 1. pure identifiers
	'commune.code_insee',
	'commune.nom_commune',
	'commune.code_departement',
	'commune.code_region',
	'commune.nom_departement',
	'commune.nom_region',
	'bien.cadastre.id_parcelle',
	'bien.cadastre.section',
	'bien.cadastre.numero',
	// 04-06: identifiers of the themes of the place (a cadastral id, an RNB id)
	'adresse.parcelle.identifiant',
	'adresse.batiment.rnb_id',
	// 2. structural-gap placeholders (glossary copy only, no metric ℹ️)
	'commune.risques.risques_naturels',
	'commune.propriete.coproprietes',
	// 3. millésime carriers, folded into their sibling metrics' millésime slot (D-13/D-18)
	'demographie.annee',
	'revenus.annee',
	'vacance.annee',
	'fiscalite.annee',
	'loyers.annee',
	'equipements.annee',
	'loyers.prediction.annee',
	// 3 bis. yearly price series: its years are the period of the row, its thresholds are said in the
	// « Méthode » sentence of its panel; neither is a metric with a popover of its own.
	'dvf.tendance.annee',
	'dvf.tendance.seuil'
	// 04.1-12 (C51) and 04.1-13 (D-127): `dpe.document.conso_ep_m2`, `dpe.document.emissions_ges_m2`
	// and `adresse.dpe.documents_adresse_brute` were excluded here, served but not displayed, until
	// the reading redesign displayed them (plans 04.5-12 and 04.5-05); they are covered below.
] as const;

/**
 * Every field_id that MUST have an explanation. Listed literally (not derived at runtime) so the
 * type system can check exhaustiveness at BUILD time — a derived array would only give a runtime
 * check. Order mirrors `fiche-fields.v1.json` to keep review diffs readable; the set-equality test
 * guarantees the two stay in sync.
 */
export const COVERED_FIELD_IDS = [
	// --- Commune scope ---
	'demographie.population',
	'demographie.nb_menages',
	'demographie.nb_logements',
	'demographie.nb_logements_vacants_recensement',
	'demographie.densite',
	'demographie.superficie',
	'revenus.revenu_median',
	'revenus.taux_pauvrete',
	'revenus.indice_gini',
	'revenus.part_imposee',
	'zonage_abc.zone',
	'zonage_abc.reclassement',
	'vacance.taux_vacance',
	'vacance.nb_vacants_plus_2ans',
	'fiscalite.taux_tfpb',
	'fiscalite.taux_tfpnb',
	'fiscalite.taux_th_rs',
	'fiscalite.taux_teom',
	'fiscalite.nom_epci',
	'loyers.loyer_m2_appartement',
	'loyers.loyer_m2_maison',
	'dvf.prix_m2_median',
	'dvf.nb_mutations_12m',
	'dvf.prix_m2_median_maison',
	'dvf.nb_mutations_12m_maison',
	'dvf.prix_m2_median_appartement',
	'dvf.nb_mutations_12m_appartement',
	'dvf.tendance.nb_ventes',
	'dvf.tendance.prix_m2_median',
	'dvf.tendance.evolution_pct',
	'dvf.indicateurs.maison.mediane_courante',
	'dvf.indicateurs.maison.mediane_precedente',
	'dvf.indicateurs.appartement.mediane_courante',
	'dvf.indicateurs.appartement.mediane_precedente',
	'dvf.indicateurs.maison.variation_pct',
	'dvf.indicateurs.appartement.variation_pct',
	'dpe.indicateurs.part_fg_pct',
	'dpe.dpe_passoires_pct',
	'dpe.dpe_median',
	'dpe.annee_construction_mediane',
	'equipements.nb_equipements',
	'scolaire.nb_etablissements_scolaires',
	'monuments.nb_monuments',
	'foncier_pm.nb_proprietaires_pm',
	'foncier_pm.surface_pm_ha',
	'connectivite.fibre_pct',
	// --- Bien scope ---
	'bien.dpe.etiquette_dpe',
	'bien.dpe.etiquette_ges',
	'bien.dpe.surface_habitable',
	'bien.dpe.annee_construction',
	'bien.dpe.periode_construction',
	'bien.dpe.type_batiment',
	'bien.dpe.cout_total_5_usages',
	'bien.dpe.energie_chauffage',
	'bien.dpe.isolation',
	'bien.dpe.conso_5_usages_ep',
	'bien.dvf.valeur_fonciere',
	'bien.dvf.prix_m2',
	'bien.dvf.date_mutation',
	'bien.dvf.type_local',
	'bien.dvf.surface_reelle_bati',
	'bien.dvf.nombre_pieces_principales',
	'bien.dvf.classe_mutation',
	'bien.dvf.surface_bati_totale',
	'bien.cadastre.contenance',
	'bien.risques.inondation',
	'bien.risques.sismique',
	'bien.risques.expo_argiles',
	'bien.urbanisme.zonage_libelle',
	'bien.urbanisme.type_zone',
	// --- Commune scope, nested values of /v1/commune (02-09, D3) ---
	'loyers.prediction.loyer_m2',
	'loyers.prediction.loyer_m2_bas',
	'loyers.prediction.loyer_m2_haut',
	'loyers.prediction.r2_ajuste',
	'loyers.prediction.r2',
	'loyers.prediction.nb_observations_commune',
	'loyers.prediction.nb_observations_maille',
	'loyers.prediction.nb_observations',
	'loyers.prediction.trimestre_reference',
	'loyers.prediction.surface_reference_m2',
	'loyers.prediction.surface_piece_reference_m2',
	'dvf.indicateurs.effectif',
	'dvf.indicateurs.quantiles.p10',
	'dvf.indicateurs.quantiles.p50',
	'dvf.indicateurs.quantiles.p90',
	'dpe.indicateurs.population.total',
	'dpe.indicateurs.population.classes_connues',
	'dpe.indicateurs.population.types_batiment',
	'dpe.indicateurs.population.validite',
	'dpe.indicateurs.population.remplacement',
	'dpe.indicateurs.population.regimes',
	'dpe.indicateurs.population.source_inconnue',
	'dpe.indicateurs.population.revision_non_verifiee',
	'dpe.indicateurs.distribution.classes',
	'dpe.indicateurs.distribution.inconnue',
	'dpe.indicateurs.dates_etablissement_inconnues',
	'dpe.indicateurs.modes',
	'dpe.indicateurs.mediane_ordinale',
	// --- Address scope (04-03): definitions of the landmarks and DPE document cards ---
	'adresse.dpe.nombre_declares',
	// 04-07: the « most recent document » row of the address comparator (definition decided by the
	// owner, 04-DECISIONS-TEXTES row 53; was excluded provisionally)
	'adresse.dpe.plus_recent',
	// 04-19: D-110 finding of the address (DPE-01, P-D110-NULL)
	'adresse.dpe.constat_d110',
	// 04.1-13 (D-127), displayed by plan 04.5-05: the group attached by the exact raw address
	'adresse.dpe.documents_adresse_brute',
	'dpe.document.etiquette_dpe',
	'dpe.document.etiquette_ges',
	'dpe.document.surface_habitable',
	// 04-19: declared address of a document card (P-ADR-01)
	'dpe.document.adresse_declaree',
	// 04-04: landmarks and characteristics of the document page (LIEU-03)
	'dpe.document.conso_ep',
	// 04.5-12 (C1, report 4.1-12): landmarks ① and ② of the document page, the per-m² values
	'dpe.document.conso_ep_m2',
	'dpe.document.emissions_ges_m2',
	'dpe.document.emissions_ges',
	'dpe.document.cout_annuel',
	'dpe.document.type_batiment',
	'dpe.document.periode_construction',
	'dpe.document.energie_chauffage',
	'dpe.document.installation_chauffage',
	'dpe.document.isolation',
	// 04-10: rows of the DPE document comparator (decision n° 63, 04-DECISIONS-TEXTES row 76)
	'dpe.document.date_etablissement',
	'dpe.document.date_fin_validite',
	'dpe.document.methode',
	'dpe.document.adresse_ban',
	'adresse.dvf.ventes_parcelle.nombre',
	'adresse.dvf.ventes_parcelle.derniere',
	'adresse.risques.argiles_au_point',
	'adresse.reference_commune.prix_median_maison',
	'adresse.reference_commune.prix_median_appartement',
	// 04-05: cards of the sales of the parcel of the point and of the similar sales (D-114, D-115)
	'adresse.dvf.vente.prix',
	'adresse.dvf.vente.prix_m2',
	'adresse.dvf.vente.surface_batie',
	'adresse.dvf.ventes_ressemblantes.effectif',
	// C37: the sales declared at the address (provisional definition, 04-DECISIONS-TEXTES row 69)
	'adresse.dvf.ventes_adresse.nombre',
	// 04-06: themes of the place (D-104, D-115)
	'adresse.batiments.nombre_parcelle',
	'adresse.batiments.nombre_parcelle_hors_annexe',
	'adresse.batiment.hauteur',
	'adresse.batiment.etages',
	'adresse.parcelle.contenance',
	'adresse.copropriete.immatriculation',
	'adresse.risques.liste_commune',
	'adresse.risques.radon_commune',
	'adresse.risques.icpe_commune',
	'adresse.urbanisme.zone_plu',
	'adresse.urbanisme.prescriptions',
	'adresse.connectivite.eligibilite_fibre',
	'adresse.connectivite.debit',
	'adresse.transports.arret_proche',
	// 04.7-10: key figures of a departement page (rattachement `departement`)
	'departement.population.total',
	'departement.population.communes_comptees',
	'departement.prix.appartement.mediane_m2',
	'departement.prix.appartement.effectif',
	'departement.prix.maison.mediane_m2',
	'departement.prix.maison.effectif',
	'departement.dpe.part_fg_pct',
	'departement.dpe.classes_connues'
] as const;

export type CoveredFieldId = (typeof COVERED_FIELD_IDS)[number];

/**
 * The dictionary. `satisfies` (not `:`) is deliberate — it enforces the exhaustive
 * `Record<CoveredFieldId, ExplicationEntry>` shape at compile time WHILE keeping the literal key
 * type, so `getExplication` indexing stays type-safe (D-16).
 *
 * Writing rules applied to every entry below:
 *   - unit spelled out in words ("en points de pourcentage", "en euros par mètre carré et par
 *     mois"), never a bare symbol the reader has to decode;
 *   - the frequent misreading named explicitly when there is one (revenu médian vs revenu de
 *     ménage, raccordable vs abonné, prix DVF vs estimation, surface DPE vs loi Carrez);
 *   - NEVER a vintage, NEVER a reference value — both are live/elsewhere (D-13).
 */
const EXPLICATIONS = {
	// --- démographie (recensement INSEE) ---
	'demographie.population': {
		definition:
			"Nombre total d'habitants recensés dans la commune, en personnes. Il s'agit de la population municipale publiée par l'INSEE (Institut national de la statistique et des études économiques)."
	},
	'demographie.nb_menages': {
		definition:
			"Nombre de ménages, c'est-à-dire de groupes de personnes partageant le même logement, quel que soit leur lien de parenté. Une personne vivant seule compte pour un ménage."
	},
	'demographie.nb_logements': {
		definition:
			'Nombre total de logements de la commune, occupés ou non : résidences principales, résidences secondaires et logements vacants réunis.'
	},
	'demographie.nb_logements_vacants_recensement': {
		definition:
			"Nombre de logements déclarés inoccupés lors du recensement, en logements. Ce comptage est distinct de LOVAC, qui couvre le parc privé et distingue la vacance toutes durées de la vacance de longue durée. Les périmètres, sources et périodes diffèrent : les deux chiffres ne sont pas directement comparables."
	},
	'demographie.densite': {
		definition:
			"Nombre d'habitants par kilomètre carré, calculé à partir de la surface administrative de la commune. Une densité faible peut recouvrir un bourg dense entouré de terres agricoles."
	},
	'demographie.superficie': {
		definition:
			"Surface administrative de la commune, en kilomètres carrés, mesurée sur sa géométrie officielle — et non par addition des parcelles cadastrales, qui n'en couvrent pas la totalité."
	},

	// --- revenus (dispositif Filosofi de l'INSEE) ---
	'revenus.revenu_median': {
		definition:
			"Revenu déclaré médian annuel par unité de consommation, en euros, avant redistribution (Filosofi DEC, Q221). Ce revenu ne mesure pas le niveau de vie après impôts et prestations. La moitié des personnes se situe en dessous et l’autre moitié au-dessus. Une valeur non publiée (secret statistique) reste indisponible, jamais zéro."
	},
	'revenus.taux_pauvrete': {
		definition:
			"Taux de pauvreté au seuil de 60 % du niveau de vie médian métropolitain, en points de pourcentage. Calculé sur le revenu disponible après redistribution (Filosofi DISP_PAUVRES, TP6021), distinct du revenu déclaré."
	},
	'revenus.indice_gini': {
		definition:
			"Indice de Gini du revenu déclaré par unité de consommation (Filosofi DEC), entre 0 et 1."
	},
	'revenus.part_imposee': {
		definition:
			"Part des ménages fiscaux imposés, en points de pourcentage (Filosofi DEC, PMIMP21)."
	},

	// --- zonage A/B/C (ministère chargé du logement) ---
	'zonage_abc.zone': {
		definition:
			"Classement officiel de la commune selon la tension du marché du logement, de A bis (le plus tendu) à C (le plus détendu), en passant par A, B1 et B2. Il conditionne l'accès à plusieurs dispositifs d'aide au logement et les plafonds qui vont avec."
	},
	'zonage_abc.reclassement': {
		definition:
			"Indique si la commune a demandé à changer de zone du classement A/B/C. Une demande déposée ne préjuge pas de son acceptation : tant que l'arrêté n'a pas changé, la zone en vigueur reste celle affichée."
	},

	// --- vacance (fichier LOVAC) ---
	'vacance.taux_vacance': {
		definition:
			"Part des logements du parc privé recensés vacants dans LOVAC, rapportée au nombre de logements du parc privé de la commune, en pourcentage. Les logements du parc social sont exclus de ce périmètre."
	},
	'vacance.nb_vacants_plus_2ans': {
		definition:
			"Nombre de logements du parc privé recensés vacants depuis plus de deux ans dans LOVAC, en logements. Ce décompte de vacance longue exclut le parc social."
	},

	// --- fiscalité locale (jeu « Fiscalité locale des particuliers » de la DGFiP) ---
	// D6 (2026-08-22) — refus explicite de la « taxe foncière moyenne au m² ». Cette définition
	// nomme ce qui est affiché (le taux voté), ce qui manque (la valeur locative cadastrale, les
	// taux départementaux et syndicaux) et refuse nommément le chiffre attendu, plutôt que de
	// laisser le lecteur lire le taux comme un montant. Ne pas raccourcir en retirant le refus.
	'fiscalite.taux_tfpb': {
		definition:
			"Taux voté par la commune pour la TFPB (taxe foncière sur les propriétés bâties), en points de pourcentage : c'est le taux, pas le montant dû. Ce montant dépend de la valeur locative cadastrale du bien, qui n'est publiée nulle part en donnée ouverte, et s'y ajoutent des taux départementaux et syndicaux que nous ne collectons pas. Nous ne calculons donc pas de taxe foncière moyenne au mètre carré."
	},
	'fiscalite.taux_tfpnb': {
		definition:
			"Taux voté pour la TFPNB (taxe foncière sur les propriétés non bâties), en points de pourcentage. Elle porte sur les terrains nus, agricoles ou forestiers, pas sur les constructions."
	},
	'fiscalite.taux_th_rs': {
		definition:
			"Taux voté pour la taxe d'habitation sur les résidences secondaires, en points de pourcentage. La taxe d'habitation sur la résidence principale, elle, a été supprimée."
	},
	'fiscalite.taux_teom': {
		definition:
			"Taux voté pour la TEOM (taxe d'enlèvement des ordures ménagères), en points de pourcentage. Elle finance la collecte et le traitement des déchets et s'ajoute à la taxe foncière."
	},
	'fiscalite.nom_epci': {
		definition:
			"Nom de l'intercommunalité (EPCI, établissement public de coopération intercommunale) à laquelle la commune appartient. Une partie des taux d'imposition et des services publics se décide à cet échelon, pas à la mairie."
	},

	// --- loyers (Carte des loyers, ANIL) ---
	'loyers.loyer_m2_appartement': {
		definition:
			"Loyer d'annonce prédit par le modèle ANIL, charges comprises, en euros par mètre carré et par mois pour un appartement type non meublé. Ce n'est ni une médiane ni un plafond réglementaire. Le bien type, le trimestre, la maille d'estimation et la qualité figurent dans le contexte lorsqu'ils ont été conservés à l'import ; ils restent inconnus sinon. Les cartes successives ne fournissent pas une évolution à périmètre constant."
	},
	'loyers.loyer_m2_maison': {
		definition:
			"Loyer d'annonce prédit par le modèle ANIL, charges comprises, en euros par mètre carré et par mois pour une maison type non meublée. Ce n'est ni une médiane ni un plafond réglementaire. Le bien type, le trimestre, la maille d'estimation et la qualité figurent dans le contexte lorsqu'ils ont été conservés à l'import ; ils restent inconnus sinon. Les cartes successives ne fournissent pas une évolution à périmètre constant."
	},

	// --- marché immobilier commune (DVF) ---
	'dvf.prix_m2_median': {
		definition:
			"Prix médian au mètre carré des ventes enregistrées dans la commune, en euros : la moitié des ventes s'est faite en dessous, l'autre au-dessus. Source : DVF (demandes de valeurs foncières), le fichier public des ventes issu des actes notariés (des prix réellement signés, jamais des annonces). Les ventes en l'état futur d'achèvement (VEFA, achat sur plan) sont exclues de cet indicateur afin de ne pas mélanger les ventes dans le neuf et dans l'ancien."
	},
	'dvf.nb_mutations_12m': {
		definition:
			"Nombre de ventes immobilières enregistrées dans la commune sur les douze derniers mois, d'après le fichier DVF (demandes de valeurs foncières). Un chiffre faible rend le prix médian moins représentatif."
	},
	'dvf.prix_m2_median_maison': {
		definition:
			"Prix médian au mètre carré des ventes de maisons enregistrées dans la commune, en euros : la moitié de ces ventes s'est faite en dessous, l'autre au-dessus. Les ventes en l'état futur d'achèvement (VEFA, achat sur plan) sont exclues de cet indicateur afin de ne pas mélanger les ventes dans le neuf et dans l'ancien."
	},
	'dvf.nb_mutations_12m_maison': {
		definition:
			"Nombre de ventes de maisons retenues pour calculer ce prix médian sur les douze derniers mois, d'après le fichier DVF (demandes de valeurs foncières). Sous un seuil de publication, le prix médian n'est pas affiché : il désignerait une transaction identifiable."
	},
	'dvf.prix_m2_median_appartement': {
		definition:
			"Prix médian au mètre carré des ventes d'appartements enregistrées dans la commune, en euros : la moitié de ces ventes s'est faite en dessous, l'autre au-dessus. Les ventes en l'état futur d'achèvement (VEFA, achat sur plan) sont exclues de cet indicateur afin de ne pas mélanger les ventes dans le neuf et dans l'ancien."
	},
	'dvf.nb_mutations_12m_appartement': {
		definition:
			"Nombre de ventes d'appartements retenues pour calculer ce prix médian sur les douze derniers mois, d'après le fichier DVF (demandes de valeurs foncières). Sous un seuil de publication, le prix médian n'est pas affiché : il désignerait une transaction identifiable."
	},
	'dvf.tendance.nb_ventes': {
		definition:
			"Nombre de ventes retenues sur une année civile, ou sur toute la période, d'après le fichier DVF (demandes de valeurs foncières) : ventes réelles, hors ventes sur plan, situées dans une seule commune. Maisons et appartements sont comptés séparément."
	},
	'dvf.tendance.prix_m2_median': {
		definition:
			"Prix médian au mètre carré des ventes retenues sur une année civile, en euros : la moitié de ces ventes s'est faite en dessous, l'autre au-dessus. En dessous d'un nombre minimal de ventes dans l'année, il n'est pas publié."
	},
	'dvf.tendance.evolution_pct': {
		definition:
			"Écart en pourcentage entre le prix médian au mètre carré des ventes de la première année et celui de la dernière année de la période, maisons et appartements séparés. Le prix médian suit ce qui s'est vendu chaque année : ce n'est ni l'évolution du prix d'un logement, ni une prévision. L'écart n'est chiffré que lorsque ces deux années comptent chacune assez de ventes."
	},
	'dvf.indicateurs.maison.mediane_courante': {
		definition: 'Prix médian au mètre carré des ventes de maisons retenues sur la période récente. La période et l’effectif transmis par le service figurent avec la cellule.'
	},
	'dvf.indicateurs.maison.mediane_precedente': {
		definition: 'Prix médian au mètre carré des ventes de maisons retenues sur la période précédente. La période et l’effectif transmis par le service figurent avec la cellule.'
	},
	'dvf.indicateurs.appartement.mediane_courante': {
		definition: 'Prix médian au mètre carré des ventes d’appartements retenues sur la période récente. La période et l’effectif transmis par le service figurent avec la cellule.'
	},
	'dvf.indicateurs.appartement.mediane_precedente': {
		definition: 'Prix médian au mètre carré des ventes d’appartements retenues sur la période précédente. La période et l’effectif transmis par le service figurent avec la cellule.'
	},
	'dvf.indicateurs.maison.variation_pct': {
		definition: 'Variation relative en pourcentage entre les prix médians des deux fenêtres transmises pour les maisons. Un zéro signifie une médiane inchangée.'
	},
	'dvf.indicateurs.appartement.variation_pct': {
		definition: 'Variation relative en pourcentage entre les prix médians des deux fenêtres transmises pour les appartements. Un zéro signifie une médiane inchangée.'
	},
	'dpe.indicateurs.part_fg_pct': {
		definition: 'Part des diagnostics disponibles classés F ou G parmi ceux dont la classe est connue, en points de pourcentage. Elle ne décrit pas tout le parc de logements.'
	},

	// --- performance énergétique commune (DPE / ADEME) ---
	'dpe.dpe_passoires_pct': {
		definition:
			'Part des diagnostics de performance énergétique (DPE) de la commune classés F ou G, en points de pourcentage, parmi les diagnostics dont la classe est connue. Un DPE est un document : cette part ne décrit pas tout le parc de logements.'
	},
	'dpe.dpe_median': {
		definition:
			'Classe énergie du milieu quand les diagnostics de performance énergétique (DPE) de la commune dont la classe est connue sont rangés de A (très performant) à G (très énergivore).'
	},
	'dpe.annee_construction_mediane': {
		definition:
			"Année de construction médiane des logements ayant fait l'objet d'un DPE (diagnostic de performance énergétique) : la moitié ont été construits avant, l'autre moitié après. Elle ne décrit que les logements ayant un DPE, pas tout le bâti de la commune."
	},

	// --- équipements, scolaire, patrimoine, foncier, réseaux ---
	'equipements.nb_equipements': {
		definition:
			"Somme des effectifs des lignes disponibles dans la base permanente des équipements de l'INSEE, au dernier millésime disponible pour cette commune. Ce stock observé ne prouve pas l'exhaustivité des équipements physiques. Un zéro est conservé seulement lorsqu'il est présent dans les effectifs source ; une collecte absente ou incomplète reste indisponible."
	},
	'scolaire.nb_etablissements_scolaires': {
		definition:
			"Nombre de codes RNE d'établissements distincts référencés dans les secteurs scolaires disponibles pour la commune. Plusieurs segments de rue peuvent désigner le même établissement ; celui-ci peut être implanté ailleurs. Ce nombre n'est pas un recensement exhaustif des établissements de la commune."
	},
	'monuments.nb_monuments': {
		definition:
			"Nombre de références de notices Mérimée distinctes disponibles pour la commune. Plusieurs lignes portant la même référence comptent une seule fois. Ce stock de notices ne représente pas nécessairement autant d'objets physiques et ne prouve pas l'exhaustivité du patrimoine."
	},
	'foncier_pm.nb_proprietaires_pm': {
		definition:
			"Nombre de SIREN distincts de personnes morales dans les droits fonciers disponibles pour la commune. Les répétitions liées à plusieurs parcelles ou droits ne multiplient pas ce compteur. Le stock observé ne prouve pas l'exhaustivité des propriétaires ; des identifiants incomplets laissent le nombre indisponible."
	},
	'foncier_pm.surface_pm_ha': {
		definition:
			'Somme des contenances des parcelles distinctes présentes dans les droits fonciers disponibles, en hectares. Les droits répétés sur une parcelle ne multiplient pas sa surface. Une contenance absente ou contradictoire rend le total indisponible. Ce stock ne prouve pas l’exhaustivité du foncier ; un hectare vaut 10 000 mètres carrés.'
	},
	'connectivite.fibre_pct': {
		definition:
			"Part des identifiants d'immeubles distincts avec une technologie fibre FO parmi les immeubles présents dans la source chargée, en points de pourcentage. Les lignes de plusieurs opérateurs ne multiplient pas les immeubles. Ce taux ne mesure ni des adresses ni des logements et ne prouve pas l'exhaustivité territoriale ; une population inconnue ou incomplète reste indisponible."
	},

	// --- bien : diagnostic de performance énergétique ---
	'bien.dpe.etiquette_dpe': {
		definition:
			"Classe énergétique du logement, de A (très performant) à G (très énergivore), issue de son DPE (diagnostic de performance énergétique) déposé auprès de l'ADEME (Agence de la transition écologique). Elle porte sur ce logement précis, pas sur l'immeuble entier."
	},
	'bien.dpe.etiquette_ges': {
		definition:
			"Classe d'émissions de GES (gaz à effet de serre) du logement, de A (faibles émissions) à G (fortes émissions). Elle est distincte de la classe énergie : un logement peu gourmand peut mal se classer ici s'il se chauffe au fioul, et inversement."
	},
	'bien.dpe.surface_habitable': {
		definition:
			"Surface habitable du logement retenue par le diagnostic, en mètres carrés. Elle peut différer de la surface annoncée dans un acte de vente (loi Carrez), qui obéit à des règles de mesure différentes."
	},
	'bien.dpe.annee_construction': {
		definition:
			"Année de construction du logement telle que déclarée dans son diagnostic. Elle n'est pas renseignée pour tous les logements ; à défaut, seule une tranche de période est connue."
	},
	'bien.dpe.periode_construction': {
		definition:
			"Tranche de période de construction du logement — par exemple 1948-1974 —, utilisée quand l'année exacte n'a pas été déclarée dans le diagnostic. Ces tranches correspondent aux grandes étapes de la réglementation thermique."
	},
	'bien.dpe.type_batiment': {
		definition:
			'Nature du bâtiment diagnostiqué : maison individuelle, appartement ou immeuble collectif.'
	},
	'bien.dpe.cout_total_5_usages': {
		definition:
			"Dépense d'énergie annuelle estimée du logement, en euros par an, pour les cinq usages du DPE (diagnostic de performance énergétique) : chauffage, eau chaude sanitaire, refroidissement, éclairage et auxiliaires. C'est une simulation à conditions d'occupation standardisées, pas une facture constatée."
	},
	'bien.dpe.energie_chauffage': {
		definition:
			"Énergie principale utilisée pour chauffer le logement — électricité, gaz, fioul, bois, réseau de chaleur —, telle que relevée par le diagnostic."
	},
	'bien.dpe.isolation': {
		definition:
			"Appréciation portée par le diagnostic sur la qualité d'isolation de l'enveloppe du logement : murs, toiture, plancher bas et ouvrants."
	},
	'bien.dpe.conso_5_usages_ep': {
		definition:
			"Consommation annuelle totale d'énergie primaire estimée pour le logement, en kWhEP/an, pour les cinq usages du diagnostic : chauffage, eau chaude sanitaire, refroidissement, éclairage et auxiliaires. Cette estimation à occupation standardisée inclut les pertes de production et d'acheminement de l'énergie ; elle ne représente pas une facture."
	},

	// --- bien : dernière vente connue (DVF) ---
	'bien.dvf.valeur_fonciere': {
		definition:
			"Prix total inscrit dans l'acte de la dernière vente connue pour ce bien, en euros, d'après DVF (demandes de valeurs foncières). Ce montant peut couvrir plusieurs lots vendus ensemble (un appartement et son garage, par exemple)."
	},
	'bien.dvf.prix_m2': {
		definition:
			"Prix au mètre carré de la dernière vente connue, en euros : le prix de l'acte rapporté à la surface bâtie déclarée. C'est un prix passé, pas une estimation de la valeur actuelle du bien."
	},
	'bien.dvf.date_mutation': {
		definition:
			"Date de la dernière vente connue pour ce bien. Les ventes sont publiées avec plusieurs mois de décalage : une transaction récente peut ne pas encore y figurer."
	},
	'bien.dvf.type_local': {
		definition:
			"Nature du local vendu lors de la dernière mutation : maison, appartement, dépendance ou local d'activité."
	},
	'bien.dvf.surface_reelle_bati': {
		definition:
			"Surface bâtie déclarée dans l'acte de la dernière vente, en mètres carrés. Elle est déclarative et peut différer de la surface habitable relevée par un diagnostic."
	},
	'bien.dvf.nombre_pieces_principales': {
		definition:
			"Nombre de pièces principales déclarées dans l'acte de vente, c'est-à-dire les chambres et les pièces de séjour. La cuisine, la salle de bains et les couloirs n'y sont pas comptés."
	},
	'bien.dvf.classe_mutation': {
		definition:
			"Ce sur quoi la transaction a porté : un logement seul, plusieurs logements vendus ensemble, un mélange de logement et de local commercial, un local d'activité ou un terrain. C'est cette nature qui décide si un prix peut être rapporté à un logement, car un prix global ne se divise pas."
	},
	'bien.dvf.surface_bati_totale': {
		definition:
			"Surface bâtie totale de la transaction, en mètres carrés, additionnée sur tous les locaux qu'elle contient. Quand la vente porte sur plusieurs locaux, ce n'est donc pas la surface du seul logement consulté."
	},

	// --- bien : parcelle, risques, urbanisme ---
	'bien.cadastre.contenance': {
		definition:
			"Surface de la parcelle cadastrale, en mètres carrés : le terrain sur lequel le bien est implanté, et non la surface habitable de la construction qui s'y trouve."
	},
	'bien.risques.inondation': {
		definition:
			"Indique si l'inondation figure dans la liste des risques recensés par Géorisques pour la commune. Une présence dans cette liste ne démontre pas que le bien est inondable ; une absence ne certifie pas l'absence de risque sur le terrain."
	},
	'bien.risques.sismique': {
		definition:
			"Indique si le séisme figure dans la liste des risques recensés par Géorisques pour la commune. Cette présence ne précise pas le niveau de sismicité, ne décrit pas la vulnérabilité du bâtiment et ne prédit aucun séisme."
	},
	'bien.risques.expo_argiles': {
		definition:
			"Niveau d'exposition au retrait-gonflement des argiles au point géographique consulté, d'après Géorisques. Ce phénomène fait gonfler puis se rétracter les sols argileux au gré de l'humidité et peut fissurer les constructions. Cette information ne constitue pas une étude de sol du terrain."
	},
	'bien.urbanisme.zonage_libelle': {
		definition:
			"Libellé de la zone du plan local d'urbanisme (PLU) renvoyée pour le point géographique consulté. Le zonage encadre les possibilités de construction ; cette réponse ne décrit pas nécessairement toutes les zones qui recoupent une parcelle."
	},
	'bien.urbanisme.type_zone': {
		definition:
			"Grande catégorie de la zone du plan local d'urbanisme renvoyée au point consulté : urbaine, à urbaniser, agricole ou naturelle. Les règles de construction dépendent du règlement de cette zone ; ce champ ne décrit pas toute la parcelle."
	},

	// --- valeurs imbriquées de /v1/commune : prédiction de loyer, indicateurs DVF et DPE ---
	'loyers.prediction.loyer_m2': {
		definition:
			"Loyer d'annonce prédit par le modèle de l'ANIL (Agence nationale pour l'information sur le logement), charges comprises, en euros par mètre carré et par mois, pour le bien type non meublé du type d'habitat (appartement ou maison). Ce n'est ni une médiane ni un plafond réglementaire."
	},
	'loyers.prediction.loyer_m2_bas': {
		definition:
			"Borne basse de l'intervalle de prédiction à 95 % du loyer d'annonce, en euros par mètre carré et par mois, publiée par l'ANIL (Agence nationale pour l'information sur le logement). Ce n'est pas un quartile : l'intervalle décrit l'incertitude du modèle, pas la dispersion des loyers."
	},
	'loyers.prediction.loyer_m2_haut': {
		definition:
			"Borne haute de l'intervalle de prédiction à 95 % du loyer d'annonce, en euros par mètre carré et par mois, publiée par l'ANIL (Agence nationale pour l'information sur le logement). Ce n'est pas un quartile : l'intervalle décrit l'incertitude du modèle, pas la dispersion des loyers."
	},
	'loyers.prediction.r2_ajuste': {
		definition:
			"Coefficient de détermination ajusté (R² ajusté) du modèle de l'ANIL (Agence nationale pour l'information sur le logement), sans unité et au plus égal à 1 : plus il est proche de 1, mieux le modèle reproduit les loyers observés. Ce n'est pas une probabilité."
	},
	'loyers.prediction.r2': {
		definition:
			"Ancien nom du coefficient de détermination ajusté (R² ajusté) du modèle de l'ANIL (Agence nationale pour l'information sur le logement) : même valeur, conservée pour compatibilité, sans unité et au plus égale à 1."
	},
	'loyers.prediction.nb_observations_commune': {
		definition:
			"Nombre d'annonces de location observées dans la commune et utilisées par l'ANIL (Agence nationale pour l'information sur le logement). Un zéro signifie qu'aucune annonce n'a été observée dans la commune : l'estimation vient alors d'un territoire plus large."
	},
	'loyers.prediction.nb_observations_maille': {
		definition:
			"Nombre d'annonces de location observées dans la maille d'estimation, le territoire regroupant plusieurs communes utilisé par l'ANIL (Agence nationale pour l'information sur le logement). Il ne s'ajoute pas au nombre d'annonces de la commune."
	},
	'loyers.prediction.nb_observations': {
		definition:
			"Ancien nom du nombre d'annonces de location observées dans la commune par l'ANIL (Agence nationale pour l'information sur le logement) : même valeur, conservée pour compatibilité."
	},
	'loyers.prediction.trimestre_reference': {
		definition:
			"Trimestre de l'année auquel se rapportent les annonces de la carte des loyers, de 1 à 4, fixé par le guide officiel du millésime. Il situe la prédiction dans l'année ; il ne décrit pas une évolution."
	},
	'loyers.prediction.surface_reference_m2': {
		definition:
			"Surface habitable, en mètres carrés, du logement type pour lequel le loyer est prédit, fixée par le guide officiel de la carte des loyers. Un logement plus grand ou plus petit se loue en général à un prix au mètre carré différent."
	},
	'loyers.prediction.surface_piece_reference_m2': {
		definition:
			"Surface moyenne par pièce, en mètres carrés, du logement type pour lequel le loyer est prédit, fixée par le guide officiel de la carte des loyers."
	},
	'dvf.indicateurs.effectif': {
		definition:
			"Nombre de ventes résidentielles retenues dans la fenêtre d'un an pour la catégorie (maisons ou appartements), d'après le fichier DVF (demandes de valeurs foncières) : un seul logement par vente, hors ventes sur plan (VEFA, vente en l'état futur d'achèvement) et hors ventes sur plusieurs communes. Un petit nombre rend le prix médian peu représentatif."
	},
	'dvf.indicateurs.quantiles.p10': {
		definition:
			"Prix au mètre carré, en euros, sous lequel se situent 10 % des ventes résidentielles de la catégorie dans la fenêtre d'un an, d'après le fichier DVF (demandes de valeurs foncières). Il n'est publié que lorsque le calcul a été qualifié."
	},
	'dvf.indicateurs.quantiles.p50': {
		definition:
			"Prix au mètre carré, en euros, sous lequel se situent la moitié des ventes résidentielles de la catégorie dans la fenêtre d'un an, d'après le fichier DVF (demandes de valeurs foncières) : c'est le prix médian. Il n'est publié que lorsque le calcul a été qualifié."
	},
	'dvf.indicateurs.quantiles.p90': {
		definition:
			"Prix au mètre carré, en euros, sous lequel se situent 90 % des ventes résidentielles de la catégorie dans la fenêtre d'un an, d'après le fichier DVF (demandes de valeurs foncières). Il n'est publié que lorsque le calcul a été qualifié."
	},
	'dpe.indicateurs.population.total': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) disponibles pour la commune, une fois retirés ceux que la source a supprimés. Ce n'est pas le nombre de logements de la commune."
	},
	'dpe.indicateurs.population.classes_connues': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) de la commune dont la classe énergie, de A à G, est connue."
	},
	'dpe.indicateurs.population.types_batiment': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) par type de bâtiment déclaré : maison, appartement, autre (immeuble compris) ou inconnu. Les quatre nombres font ensemble le total des diagnostics."
	},
	'dpe.indicateurs.population.validite': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) selon leur validité à la date d'évaluation : encore valide d'après ses dates, expiré, dates incohérentes, établi après cette date, ou validité inconnue. Les nombres font ensemble le total des diagnostics."
	},
	'dpe.indicateurs.population.remplacement': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) selon qu'un diagnostic plus récent les remplace : remplacé, aucun successeur connu, remplacement ambigu ou non vérifié. Les nombres font ensemble le total des diagnostics."
	},
	'dpe.indicateurs.population.regimes': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) par jeu de données de l'ADEME (Agence de l'environnement et de la maîtrise de l'énergie) : logements existants, logements neufs, ou inconnu. Un diagnostic présent dans les deux jeux compte dans chacun : ces nombres ne s'additionnent pas."
	},
	'dpe.indicateurs.population.source_inconnue': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) dont l'état dans la source ADEME (Agence de l'environnement et de la maîtrise de l'énergie) n'est pas connu."
	},
	'dpe.indicateurs.population.revision_non_verifiee': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) dont la version la plus récente n'a pas été vérifiée. Aujourd'hui aucune révision n'est vérifiée : ce nombre égale le total."
	},
	'dpe.indicateurs.distribution.classes': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) de la commune dans chaque classe énergie, de A (très performant) à G (très énergivore)."
	},
	'dpe.indicateurs.distribution.inconnue': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) de la commune sans classe énergie connue."
	},
	'dpe.indicateurs.dates_etablissement_inconnues': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) de la commune dont la date d'établissement n'est pas connue."
	},
	// D-46 — read from the contract descriptions of the two entries.
	'dpe.indicateurs.modes': {
		definition:
			"Classes énergie les plus fréquentes parmi les diagnostics de performance énergétique (DPE) de la commune dont la classe est connue. En cas d'égalité, toutes sont données, de A à G."
	},
	'dpe.indicateurs.mediane_ordinale': {
		definition:
			"Classe énergie du milieu quand les diagnostics de performance énergétique (DPE) de la commune dont la classe est connue sont rangés de A à G. Absente si aucune classe n'est connue."
	},
	// 04-03 — read from the contract descriptions of the address entries.
	'adresse.dpe.nombre_declares': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) dont l'adresse saisie par le diagnostiqueur correspond à cette adresse. Aucun n'est rattaché à un logement précis."
	},
	// 04-DECISIONS-TEXTES row 53 (owner's wording).
	'adresse.dpe.plus_recent': {
		definition:
			"Diagnostic de performance énergétique (DPE) dont la date d'établissement est la plus récente parmi ceux déclarés à cette adresse. Il n'est rattaché à aucun logement."
	},
	'adresse.dpe.constat_d110': {
		definition:
			"Constat donné seulement quand tout concorde : une maison, un seul diagnostic de performance énergétique (DPE) à cette adresse, une adresse saisie identique, une seule parcelle sous le point de l'adresse et un seul bâtiment dessus, hors annexes. Il ne prouve pas l'identité d'un logement."
	},
	// 04.1-13 (D-127): the description of the contract, word for word (plan 04.5-05); to be read
	// again as a block by Ianis with the other texts of 4.1-13 (D-119 point 5, R10).
	'adresse.dpe.documents_adresse_brute': {
		definition:
			"Nombre de documents DPE déclarés à cette adresse par leur adresse saisie, rapprochée exactement de la BAN : l'identifiant d'adresse fourni par l'ADEME ne correspond à aucune adresse de la BAN, mais le numéro, le suffixe, la voie et la commune saisis désignent cette adresse et une seule. Un groupe distinct des documents de la clé ADEME, hors de leur nombre ; ce n'est pas une identité de logement."
	},
	'dpe.document.etiquette_dpe': {
		definition:
			"Classe énergie, de A (très performant) à G (très énergivore), déclarée par ce diagnostic de performance énergétique (DPE). Le document décrit ce que le diagnostiqueur a déclaré, pas un logement identifié."
	},
	'dpe.document.etiquette_ges': {
		definition:
			'Classe climat, de A (peu d’émissions de gaz à effet de serre) à G (beaucoup d’émissions), déclarée par ce diagnostic de performance énergétique (DPE).'
	},
	'dpe.document.surface_habitable': {
		definition: 'Surface habitable déclarée par ce diagnostic de performance énergétique (DPE), en mètres carrés.'
	},
	'dpe.document.adresse_declaree': {
		definition:
			"Adresse saisie par le diagnostiqueur dans ce diagnostic de performance énergétique (DPE), quand elle diffère de l'adresse retenue. Elle est affichée telle que saisie seulement si elle ne porte aucun complément ; sinon elle n'est pas affichée et le motif est donné."
	},
	// 04-04 — adapted from the `bien.dpe.*` definitions: the value is the document's, not a dwelling's.
	'dpe.document.conso_ep': {
		definition:
			"Consommation annuelle totale d'énergie primaire déclarée par ce diagnostic de performance énergétique (DPE), en kilowattheures d'énergie primaire par an, pour les cinq usages du diagnostic : chauffage, eau chaude sanitaire, refroidissement, éclairage et auxiliaires. C'est une estimation à occupation standardisée, pas une facture."
	},
	// 04.5-12 (C1): the definition of report 4.1-12 as proposed, without the ADEME column name in
	// parentheses (no technical key in a public text, guard M-15); to be read again as a block by
	// Ianis with the other texts of 4.1-12 (D-119 point 5, R10).
	'dpe.document.conso_ep_m2': {
		definition:
			"Consommation d'énergie primaire des cinq usages par mètre carré et par an, publiée par l'ADEME pour le document DPE, en kWhEP/m²/an : c'est la valeur qu'on rapproche de la classe énergie. Elle n'est jamais recalculée à partir du total et de la surface ; absente de la source, elle est non renseignée."
	},
	// 04.5-12 (C1): same rule as `dpe.document.conso_ep_m2` above (report text, no column name, R10).
	'dpe.document.emissions_ges_m2': {
		definition:
			"Émissions de gaz à effet de serre des cinq usages par mètre carré et par an, publiées par l'ADEME pour le document DPE, en kg CO₂ éq/m²/an : c'est la valeur qu'on rapproche de la classe climat. Elles ne sont jamais recalculées à partir du total et de la surface ; absentes de la source, elles sont non renseignées."
	},
	'dpe.document.emissions_ges': {
		definition:
			"Émissions annuelles totales de gaz à effet de serre déclarées par ce diagnostic de performance énergétique (DPE), en kilogrammes d'équivalent CO₂ par an, pour les cinq usages du diagnostic. C'est une estimation à occupation standardisée."
	},
	'dpe.document.cout_annuel': {
		definition:
			"Dépense d'énergie annuelle estimée déclarée par ce diagnostic de performance énergétique (DPE), en euros par an, pour les cinq usages du diagnostic : chauffage, eau chaude sanitaire, refroidissement, éclairage et auxiliaires. C'est une simulation à conditions d'occupation standardisées, pas une facture constatée."
	},
	'dpe.document.type_batiment': {
		definition:
			'Nature du bâtiment décrit par ce diagnostic de performance énergétique (DPE) : maison individuelle, appartement ou immeuble collectif, telle que déclarée par le diagnostiqueur.'
	},
	'dpe.document.periode_construction': {
		definition:
			"Tranche de période de construction déclarée par ce diagnostic de performance énergétique (DPE), par exemple 1948-1974. Ces tranches correspondent aux grandes étapes de la réglementation thermique."
	},
	'dpe.document.energie_chauffage': {
		definition:
			'Énergie principale de chauffage déclarée par ce diagnostic de performance énergétique (DPE) : électricité, gaz, fioul, bois, réseau de chaleur…'
	},
	'dpe.document.installation_chauffage': {
		definition:
			"Type d'installation de chauffage déclaré par ce diagnostic de performance énergétique (DPE) : individuelle, collective ou mixte, tel que relevé par le diagnostiqueur."
	},
	'dpe.document.isolation': {
		definition:
			"Appréciation portée par ce diagnostic de performance énergétique (DPE) sur la qualité d'isolation de l'enveloppe : murs, toiture, plancher bas et ouvrants."
	},
	// 04-10 — decision n° 63 (product owner, 2026-10-11): public wording, validated word for word.
	'dpe.document.date_etablissement': {
		definition:
			"Date à laquelle le diagnostiqueur a établi ce diagnostic de performance énergétique (DPE), telle que publiée par l'ADEME. Elle fixe la méthode de calcul du document (avant ou depuis juillet 2021)."
	},
	'dpe.document.date_fin_validite': {
		definition:
			"Date de fin de validité déclarée par ce diagnostic de performance énergétique (DPE), telle que publiée par l'ADEME. Elle n'est jamais calculée à partir de la date d'établissement ; un document dont la date est dépassée reste listé avec sa date."
	},
	'dpe.document.methode': {
		definition:
			"Méthode de calcul de ce diagnostic de performance énergétique (DPE), lue sur sa date d'établissement : ancienne méthode avant juillet 2021, méthode actuelle depuis. Les classes, consommations et émissions de deux DPE de méthodes différentes ne se comparent pas."
	},
	'dpe.document.adresse_ban': {
		definition:
			"Adresse de la Base Adresse Nationale (BAN) dont l'identifiant est déclaré par l'ADEME pour ce diagnostic de performance énergétique (DPE). DataCommune reprend cette déclaration sans la vérifier ; elle ne désigne aucun logement."
	},
	'adresse.dvf.ventes_parcelle.nombre': {
		definition:
			"Nombre de ventes et autres mutations à titre onéreux enregistrées dans les demandes de valeurs foncières (DVF) sur la parcelle cadastrale où se trouve le point de l'adresse, sur toute la période importée. Une vente de plusieurs parcelles compte une fois."
	},
	'adresse.dvf.ventes_parcelle.derniere': {
		definition:
			"Date de la vente la plus récente enregistrée dans les demandes de valeurs foncières (DVF) sur la parcelle cadastrale où se trouve le point de l'adresse."
	},
	'adresse.risques.argiles_au_point': {
		definition:
			"Exposition au retrait-gonflement des argiles au point de l'adresse, selon Géorisques le jour de la consultation. Elle ne dit rien des autres risques, recensés à l'échelle de la commune."
	},
	'adresse.reference_commune.prix_median_maison': {
		definition:
			"Prix médian au mètre carré des ventes de maisons de la commune sur les 12 derniers mois du fichier DVF importé, période indiquée sous la valeur, donné en référence. Ce n'est pas une estimation de l'adresse."
	},
	'adresse.reference_commune.prix_median_appartement': {
		definition:
			"Prix médian au mètre carré des ventes d'appartements de la commune sur les 12 derniers mois du fichier DVF importé, période indiquée sous la valeur, donné en référence. Ce n'est pas une estimation de l'adresse."
	},
	'adresse.dvf.vente.prix': {
		definition:
			"Valeur foncière déclarée dans les demandes de valeurs foncières (DVF) pour une vente : montant de toute la vente, qui peut porter sur plusieurs locaux ou parcelles. Elle n'est rattachée à aucun logement."
	},
	'adresse.dvf.vente.prix_m2': {
		definition:
			"Valeur foncière divisée par la surface bâtie déclarée, calculée seulement pour la vente d'un seul local d'habitation. Ce n'est pas une estimation de l'adresse."
	},
	'adresse.dvf.vente.surface_batie': {
		definition:
			"Surface réelle bâtie déclarée dans les demandes de valeurs foncières (DVF) pour une vente, somme de ses locaux. Elle ne décrit aucun logement précis."
	},
	'adresse.dvf.ventes_ressemblantes.effectif': {
		definition:
			"Nombre de ventes d'une seule maison ou d'un seul appartement enregistrées dans les demandes de valeurs foncières (DVF) selon les critères affichés : type, surface à la tolérance près, distance à vol d'oiseau du point de l'adresse ou commune de l'adresse, dernières années importées. Aucune vente n'est rattachée à un logement."
	},
	'adresse.dvf.ventes_adresse.nombre': {
		definition:
			// Row 73 (C44, provisional): no sale attached is not a zero. C3 (04.5-08): the sentence of the report
			// 4.1-14 replaces, at its place, the one on the suffix the imported DVF did not keep (R10).
			"Nombre de ventes enregistrées dans les demandes de valeurs foncières (DVF) rattachées à cette adresse par l'adresse saisie dans la déclaration (numéro, voie et commune), sur toute la période importée. La vente est rattachée par cette adresse déclarée, pas par la parcelle : elle peut aussi figurer parmi les ventes publiées sur la parcelle. Aucune vente rattachée ne veut pas dire aucune vente : pour une vente de plusieurs biens, seule l'adresse de sa première ligne est gardée, et une partie des ventes déclarées ne retrouve aucune adresse de la Base Adresse Nationale. La vente est rattachée par le numéro et le suffixe déclarés : une vente déclarée au « 2 » sans suffixe ne figure que sur la page du « 2 », une vente déclarée au « 2 B » sur celle du « 2 bis ». Quand un suffixe ne correspond à aucune adresse, ou à plusieurs, la vente n'est montrée sur aucune. " +
			// C4 (04.5-07): addition of the report 4.1-15, applied as written, to be read in a block by Ianis (R10).
			"Les ventes en l'état futur d'achèvement (VEFA) déclarées à l'adresse sont listées et marquées, mais ne sont pas comptées."
	},
	'adresse.batiments.nombre_parcelle': {
		definition:
			"Nombre de bâtiments recensés par la BD TOPO de l'IGN sur la parcelle cadastrale où se trouve le point de l'adresse, annexes comprises. Il ne désigne aucun bâtiment de l'adresse en particulier, ni un logement."
	},
	'adresse.batiments.nombre_parcelle_hors_annexe': {
		definition:
			"Nombre de bâtiments de la même parcelle, sans ceux dont l'usage déclaré dans la BD TOPO est « annexe » ; un bâtiment d'usage inconnu est compté."
	},
	'adresse.batiment.hauteur': {
		definition:
			"Hauteur, en mètres, du bâtiment de la BD TOPO qui contient le point de l'adresse, donnée seulement quand un seul bâtiment le contient."
	},
	'adresse.batiment.etages': {
		definition:
			"Nombre d'étages du bâtiment de la BD TOPO qui contient le point de l'adresse, donné seulement quand un seul bâtiment le contient."
	},
	'adresse.parcelle.contenance': {
		definition:
			"Surface cadastrale, en mètres carrés, de la parcelle où se trouve le point de l'adresse."
	},
	'adresse.copropriete.immatriculation': {
		definition:
			"Nombre de copropriétés du Registre national des copropriétés dont la parcelle déclarée est celle où se trouve le point de l'adresse. Aucun lot ni logement n'en est déduit."
	},
	'adresse.risques.liste_commune': {
		definition:
			"Risques naturels et technologiques que Géorisques recense pour la commune de l'adresse (base GASPAR), le jour de la consultation. C'est une référence communale : elle ne dit pas que l'adresse y est exposée."
	},
	'adresse.risques.radon_commune': {
		definition:
			"Catégorie du potentiel radon de la commune de l'adresse, de 1 (faible) à 3 (significatif), selon Géorisques. C'est une référence communale, pas une mesure à l'adresse."
	},
	'adresse.risques.icpe_commune': {
		definition:
			"Nombre d'installations classées pour la protection de l'environnement (ICPE) que Géorisques recense dans la commune de l'adresse. C'est une référence communale, pas une distance à l'adresse."
	},
	'adresse.urbanisme.zone_plu': {
		definition:
			"Zone du document d'urbanisme (plan local d'urbanisme ou carte communale) qui contient le point de l'adresse, selon le Géoportail de l'urbanisme le jour de la consultation."
	},
	'adresse.urbanisme.prescriptions': {
		definition:
			"Prescriptions du document d'urbanisme qui s'appliquent au point de l'adresse (par exemple un espace boisé classé), selon le Géoportail de l'urbanisme le jour de la consultation."
	},
	'adresse.connectivite.eligibilite_fibre': {
		definition:
			"Éligibilité à la fibre optique publiée par l'Arcep pour l'immeuble de cette adresse, donnée seulement quand un seul immeuble porte exactement ce numéro et cette voie. Elle ne dit rien d'un abonnement."
	},
	'adresse.connectivite.debit': {
		definition:
			"Classe de débit descendant que l'Arcep publie pour la fibre de cet immeuble, telle que publiée."
	},
	'adresse.transports.arret_proche': {
		definition:
			"Arrêts de transport en commun et gares les plus proches du point de l'adresse, à moins d'un kilomètre, avec leur distance à vol d'oiseau. Ce n'est pas un temps de trajet."
	},

	// --- page d'un département (04.7-10) : sommes et médianes sur l'ensemble de ses communes ---
	'departement.population.total': {
		definition:
			"Nombre d'habitants du département, en personnes : la somme des populations de ses communes dont la population est chargée, publiées par l'INSEE (Institut national de la statistique et des études économiques), chacune à son dernier recensement chargé. Le nombre de communes comptées est écrit sous le chiffre : s'il est inférieur au nombre de communes du département, le total est partiel. Paris, Lyon et Marseille comptent une fois, jamais leurs arrondissements en plus."
	},
	'departement.population.communes_comptees': {
		definition:
			"Nombre de communes dont la population entre dans le total du département. Une commune sans population chargée n'est pas comptée : ce nombre dit sur combien de communes repose le total."
	},
	'departement.prix.appartement.mediane_m2': {
		definition:
			"Prix médian des appartements du département, en euros par mètre carré, sur 12 mois glissants. Il est calculé sur l'ensemble des ventes retenues dans ses communes (DVF, Demandes de valeurs foncières, de la DGFiP), chaque vente une fois : ce n'est pas une moyenne des prix médians des communes. Il n'est pas publié sous 5 ventes."
	},
	'departement.prix.appartement.effectif': {
		definition:
			"Nombre de ventes d'appartements retenues pour le prix médian du département sur la fenêtre de 12 mois, en ventes. C'est la base du chiffre : aucune vente retenue n'est jamais présentée comme un zéro."
	},
	'departement.prix.maison.mediane_m2': {
		definition:
			"Prix médian des maisons du département, en euros par mètre carré, sur 12 mois glissants. Il est calculé sur l'ensemble des ventes retenues dans ses communes (DVF, Demandes de valeurs foncières, de la DGFiP), chaque vente une fois : ce n'est pas une moyenne des prix médians des communes. Il n'est pas publié sous 5 ventes."
	},
	'departement.prix.maison.effectif': {
		definition:
			"Nombre de ventes de maisons retenues pour le prix médian du département sur la fenêtre de 12 mois, en ventes. C'est la base du chiffre : aucune vente retenue n'est jamais présentée comme un zéro."
	},
	'departement.dpe.part_fg_pct': {
		definition:
			"Part des diagnostics de performance énergétique (DPE) classés F ou G parmi ceux dont la classe, de A à G, est connue, en points de pourcentage. C'est une part de documents (anciens, périmés et remplacés compris), pas une part de logements."
	},
	'departement.dpe.classes_connues': {
		definition:
			"Nombre de diagnostics de performance énergétique (DPE) du département dont la classe énergie, de A à G, est connue, en documents. C'est la base de la part des classes F et G."
	}
} satisfies Record<CoveredFieldId, ExplicationEntry>;

export { EXPLICATIONS };

const COVERED_SET: ReadonlySet<string> = new Set<string>(COVERED_FIELD_IDS);

/**
 * Type guard — true only for a field_id that HAS an explanation. Returns false (never throws) for
 * null/undefined and for every excluded id, so a caller can decide "render an ℹ️ trigger or not"
 * without a try/catch.
 */
/**
 * Public definition of a field for the « Définition » entry of a source panel (03-17 F66, M-15):
 * the `EXPLICATIONS` entry, never the technical `description` of the contract. A field excluded
 * from `EXPLICATIONS` (identifiers, structural milestones, vintages) or unknown has no entry: null.
 */
export function definitionPublique(fieldId: string | null): string | null {
	return hasExplication(fieldId) ? getExplication(fieldId).definition : null;
}

export function hasExplication(fieldId: string | undefined | null): fieldId is CoveredFieldId {
	return fieldId != null && COVERED_SET.has(fieldId);
}

/**
 * Typed access to one explanation. Throws LOUD on an uncovered id (same fail-closed contract as
 * `getFieldContract` in formatters.ts, T-43-07): rendering an empty popover would look like a
 * missing datum to the reader, when it is in fact a coding mistake.
 */
export function getExplication(fieldId: CoveredFieldId): ExplicationEntry {
	// Guarded through the COVERED set, never by a truthiness test on the lookup: an inherited
	// Object.prototype key ('constructor', 'toString') would pass a `!= null` check and return a
	// function where an entry is expected.
	if (!hasExplication(fieldId)) {
		throw new Error(
			`Aucune explication ℹ️ pour ce field_id (absent de COVERED_FIELD_IDS) : "${fieldId}"`
		);
	}
	return EXPLICATIONS[fieldId];
}

/**
 * Dataset owning a field_id — the join key between a metric and its live provenance.
 * Strips the optional `bien.` scope prefix, then takes the first segment:
 *   'revenus.revenu_median'      -> 'revenus'
 *   'bien.dpe.etiquette_dpe'     -> 'dpe'
 *   'zonage_abc.zone'            -> 'zonage_abc'
 */
export function datasetDuField(fieldId: string): string {
	const sansPrefixe = fieldId.startsWith('bien.') ? fieldId.slice('bien.'.length) : fieldId;
	return sansPrefixe.split('.')[0];
}

/**
 * Live millésimes carried by a /v1/commune response (D-13) — the ONLY source of a vintage on the
 * Fiche Commune. Keyed by dataset so the ℹ️ can do `millesimes[datasetDuField(fieldId)]`.
 *
 * SCOPE DECISION (49-RESEARCH Open Question 1, settled by 49-02-PLAN): `/v1/commune` is NOT
 * extended to return `DatasetProvenance` — this phase is UI-tier, no backend change. Consequence,
 * assumed and visible: only the 6 datasets exposing an `annee_*` column have a
 * millésime here, including equipment since COR-14. The 7 other commune-aggregated
 * datasets (dvf, dpe, zonage_abc, monuments, scolaire, foncier_pm, connectivite) get an HONEST ABSENCE of vintage (D-07/D-13) —
 * never a vintage borrowed from a sibling dataset, never today's date. Their SOURCE, on the other
 * hand, IS live and must be consumed: see `sourcesCommuneParDataset` below.
 */
export function millesimesFromCommune(c: CommuneSynthese): Record<string, string | null> {
	const asString = (annee: number | null | undefined): string | null =>
		annee == null ? null : String(annee);
	return {
		demographie: asString(c.annee_demographie),
		revenus: asString(c.annee_revenus),
		vacance: asString(c.annee_vacance),
		fiscalite: asString(c.annee_fiscalite),
		loyers: asString(c.annee_loyers),
		equipements: asString(c.annee_equipements)
	};
}

/**
 * Indexes the `meta.source` of /v1/resolve by dataset name. Each `DatasetProvenance` carries its
 * own `dataset` field, so this is a plain re-key — no ordering assumption, no fail-closed clause
 * needed (contrast with the commune helper below).
 */
export function provenanceParDataset(
	source: DatasetProvenance[] | null | undefined
): Record<string, DatasetProvenance> {
	const parDataset: Record<string, DatasetProvenance> = {};
	for (const provenance of source ?? []) {
		if (provenance?.dataset) parDataset[provenance.dataset] = provenance;
	}
	return parDataset;
}

/** Minimal shape of a live source attribution (Fiche Commune `meta.source`). */
export interface SourceLive {
	nom?: string;
	url?: string | null;
}

/**
 * Live publication behind a dataset — the SINGLE attribution rule of the two fiches (D-11).
 *
 * EXTRACTED from `Theme.svelte` at plan 50-07 so the printed sources table and the ℹ️ panels can
 * never disagree on who published a figure: a second copy of this precedence would be a second
 * chance to credit the wrong producer on a document that leaves the site as a PDF.
 *
 * The Fiche Bien path (`DatasetProvenance`) wins when present because it is self-keyed; the Fiche
 * Commune path (`SourceAttribution`, zipped fail-closed by `sourcesCommuneParDataset`) is the
 * fallback. A nameless attribution yields `null` — an absent source line beats a line reading
 * "Source : undefined".
 *
 * Deliberately NOT scoped by row scope, unlike the vintage precedence (`millesimePour`, just
 * below): a source is self-keyed and legitimately richer on the Fiche Bien — §7 of
 * 49-VERIFICATION.md refuted the opposite premise.
 */
export function sourcePourDataset(
	dataset: string,
	provenances: Record<string, DatasetProvenance>,
	sources: Record<string, SourceLive>
): { nom: string; url: string | null } | null {
	const provenance = provenances[dataset];
	if (provenance?.nom) return { nom: provenance.nom, url: provenance.url ?? null };
	const attribution = sources[dataset];
	if (attribution?.nom) return { nom: attribution.nom, url: attribution.url ?? null };
	return null;
}

/**
 * D-21 #5 — THE single vintage precedence, and it is scoped by the row's own scope.
 *
 * EXTRACTED from `Theme.svelte` at plan 50-12 for the very reason `sourcePourDataset` was extracted
 * at 50-07: the KPI summary component (removed since) kept a second, UNSCOPED copy of this rule, so the ℹ️ of a KPI and the
 * ℹ️ of the theme row showing the SAME commune figure could date it differently.
 *
 * WHY, because the tempting rule (« richest source first ») is the defect: `provenances` comes
 * from /v1/resolve and therefore exists ONLY on the Fiche Bien, while `millesimes`
 * (`millesimesFromCommune`) is the one derivative BOTH surfaces receive. A commune row read the
 * provenance first, so the same figure was dated from the resolve period on /bien/[token] and
 * from the `annee_*` column on /commune/[code] — two dates, one figure, zero way for the reader
 * to know which is right. The authority for a row DISPLAYED BY BOTH surfaces can only be the
 * derivative both surfaces have.
 *
 * The goal here is CONVERGENCE, not richness: on a commune row, saying exactly as much as the
 * Fiche Commune says IS the contract — saying more would BE the divergence. A bien-scoped row, on
 * the other hand, exists on one surface only, so the provenance rightly wins there.
 * (`sourcePourDataset` is deliberately NOT scoped: a source is self-keyed and legitimately richer
 * on the Fiche Bien — §7 of 49-VERIFICATION.md refuted the opposite premise.)
 */
export function millesimePour(
	dataset: string,
	portee: Scope,
	provenances: Record<string, DatasetProvenance>,
	millesimes: Record<string, string | null>
): string | null {
	if (portee === 'commune') return millesimes[dataset] ?? null;
	return provenances[dataset]?.periode?.label ?? millesimes[dataset] ?? null;
}

/**
 * READ-ONLY MIRROR of `COMMUNE_SOURCE_KEYS` (`api/backend/app/commune/schemas.py`), the
 * ordered sources of the `ReponseApi.meta` of GET /v1/commune (`app/commune/router.py`).
 * ORDER IS THE CONTRACT: unlike `DatasetProvenance`, `SourceAttribution` carries NO dataset key,
 * so the only way to attribute each entry is its rank in the list. This array must stay
 * byte-identical to the backend's, in the same order; the backend is never edited from here.
 */
export const COMMUNE_META_SOURCE_ORDER = [
	'demographie',
	'revenus',
	'zonage_abc',
	'vacance',
	'fiscalite',
	'loyers',
	'dvf',
	'dpe',
	'equipements',
	'scolaire',
	'monuments',
	'foncier_pm',
	'connectivite'
] as const;

/**
 * READ-ONLY MIRROR of the PUBLICATION NAME each dataset is served under: exactly
 * `DATASET_CATALOG[<dataset>].nom` in `api/backend/app/common/source_catalog.py`, which is what
 * `sources.py::DATASET_SOURCES` projects onto `SourceAttribution.nom` and what the ℹ️ panel prints
 * as « Source : … ». Copied CHARACTER FOR CHARACTER: the catalog is written in accented French
 * since 03-17 (K12, « Demandes de valeurs foncières (DVF) »), and this mirror follows the catalog,
 * never the reverse — a name is corrected in `source_catalog.py`, then copied here. The strings are
 * compared for equality (guard `api/backend/tests/test_commune_source_mirror_contract.py`), not
 * displayed from this file.
 *
 * Same discipline as ORDER IS THE CONTRACT above: the backend is never edited from here, and a
 * drift between this mirror and the catalog does NOT degrade into a guess — it makes the
 * attribution fall back to an honest absence (`{}`), which the reader sees as a missing source
 * line rather than as somebody else's name under the figures.
 */
export const COMMUNE_META_SOURCE_NOMS: Record<
	(typeof COMMUNE_META_SOURCE_ORDER)[number],
	string
> = {
	demographie: 'Recensement de la population',
	revenus: 'Filosofi (revenus et pauvreté)',
	zonage_abc: 'Zonage ABC (tension immobilière)',
	vacance: 'Logements vacants (LOVAC)',
	fiscalite: 'Fiscalité locale des particuliers',
	loyers: 'Carte des loyers',
	dvf: 'Demandes de valeurs foncières (DVF)',
	dpe: 'Diagnostics de performance énergétique (DPE)',
	equipements: 'Base permanente des équipements (BPE)',
	scolaire: 'Carte scolaire des collèges',
	monuments: 'Monuments historiques',
	foncier_pm: 'Fichiers des personnes morales (MAJIC)',
	connectivite: 'Ma Connexion Internet (Arcep)'
};

/** Reverse index of the name mirror: publication name → dataset key. Built once. */
const DATASET_PAR_NOM: Map<string, string> = new Map(
	Object.entries(COMMUNE_META_SOURCE_NOMS).map(([dataset, nom]) => [nom, dataset])
);

/**
 * Live SOURCE per dataset for the Fiche Commune.
 *
 * FAIL-CLOSED (T-49-12, hardened T-50-05 / D-21 #4). The failure mode guarded against is not a
 * crash but a SHIFTED attribution: `SourceAttribution` carries no dataset key, so until Phase 50
 * the entries were zipped BY RANK and the only guard was the LENGTH of the list. A backend
 * permutation kept thirteen entries, passed the length check, and silently credited LOVAC's
 * figures to the DGFiP on a public surface — a lie the reader cannot detect, unlike a missing
 * source line which he simply sees.
 *
 * Each entry is now attributed BY NAME: the dataset it is filed under is the one whose expected
 * publication name (`COMMUNE_META_SOURCE_NOMS`, mirrored from the backend catalog) it carries. Any
 * of the following yields `{}` — an honest absence of source for the whole panel:
 *   - null/undefined list, or any length other than the mirror's (unchanged);
 *   - a missing or unknown `nom` (a renamed publication is catalog drift, not a guess to make);
 *   - a name that does not match the dataset expected at that rank (permutation / reorder);
 *   - a dataset of the mirror left unprovided.
 * All-or-nothing on purpose: a partial map would print a source line under some figures and none
 * under others, with no way for the reader to know which case he is looking at.
 */
export function sourcesCommuneParDataset(
	source: SourceAttribution[] | null | undefined
): Record<string, SourceAttribution> {
	if (source == null || source.length !== COMMUNE_META_SOURCE_ORDER.length) return {};
	const parDataset: Record<string, SourceAttribution> = {};
	for (const [i, attribution] of source.entries()) {
		const nom = attribution?.nom;
		// The NAME decides the key — never the position.
		const dataset = nom == null ? undefined : DATASET_PAR_NOM.get(nom);
		if (dataset == null) return {};
		// …and the mirror must still describe the list it is being applied to: a name landing at
		// another dataset's rank means our mirror no longer matches `COMMUNE_SOURCE_KEYS`.
		if (dataset !== COMMUNE_META_SOURCE_ORDER[i]) return {};
		parDataset[dataset] = attribution;
	}
	if (Object.keys(parDataset).length !== COMMUNE_META_SOURCE_ORDER.length) return {};
	return parDataset;
}
