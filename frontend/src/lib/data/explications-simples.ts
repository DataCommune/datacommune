// Plain-language explanations of the detail panels (owner review of 09/11): « when I open the details, the
// simplest and quickest explanation of each thing, for the widest public ». Pure module, zero Svelte imports.
//
// ONE PLACE TO PROOFREAD: every « En clair » sentence of the site lives here, so that the owner reads them all
// in one file. Each sentence says what the thing is and why it matters for a dwelling, in at most 25 words,
// with no promise and no judgement; an acronym is spelled out or avoided. It is shown FIRST in a detail,
// before the definition, the method and the source, which stay as they are (`explications.ts` keeps the
// precise definitions; these sentences summarise them and never contradict them).
//
// Coverage is closed by `explications-simples.test.ts`: every theme of the backend registry
// (`api/backend/app/rapport/themes/*.py`) and every covered field_id (`COVERED_FIELD_IDS`) must have its
// sentence, and every sentence must keep the 25-word rule.
//
// The second half holds the technical flags of a read (`liste_incomplete`, `lecture_complete`…): a flag
// that says nothing to the reader (« Liste incomplète : non ») is not shown; a flag that carries a reserve is
// shown with the reason why. « Liste complète : oui » is never written: a source may not list everything.

import type { CoveredFieldId } from './explications';

/** Label of the entry, in the panels and at the head of a criterion's detail. */
export const LIBELLE_EN_CLAIR = 'En clair';

/** One sentence per theme of the address report, keyed by the served theme id. */
export const EN_CLAIR_THEMES: Readonly<Record<string, string>> = {
	// --- Le bâti et l'énergie ---
	batiment:
		'Bâtiment situé au point de l’adresse (hauteur, étages) et bâtiments de sa parcelle ; cela aide à savoir de quel bâtiment il s’agit.',
	construction:
		'Année de construction des bâtiments de la parcelle ; dans les bâtiments anciens, des matériaux comme les peintures au plomb peuvent être présents.',
	copropriete:
		'Copropriétés inscrites au registre national sur la parcelle de l’adresse ; elles signalent un immeuble partagé, avec un syndic, des charges et des règles communes.',
	dpe: 'Diagnostics de performance énergétique (DPE) publiés à l’adresse ; leur classe, de A à G, indique la consommation d’énergie estimée du logement.',

	// --- Le terrain et les risques ---
	altitude:
		'Hauteur du sol à l’adresse au-dessus du niveau de la mer ; elle aide à situer le relief mais ne dit rien, seule, d’un risque.',
	argiles:
		'Sol argileux qui gonfle quand il est humide et se rétracte quand il sèche ; ces mouvements peuvent fissurer les murs d’une maison.',
	catnat:
		'Arrêtés qui reconnaissent une catastrophe naturelle survenue dans la commune ; ils montrent des événements passés, qui peuvent se reproduire.',
	debroussaillement:
		'Obligation légale de débroussailler autour des constructions dans certaines zones, pour limiter les incendies ; elle peut s’appliquer à la parcelle de l’adresse.',
	geologie:
		'Roche dominante du secteur, lue sur une carte nationale peu détaillée ; elle décrit le sous-sol à grande échelle, pas le terrain exact de l’adresse.',
	icpe_commune:
		'Installations classées pour la protection de l’environnement (ICPE), comme des usines ou des élevages, comptées dans toute la commune ; elles peuvent causer des nuisances.',
	inondation:
		'Exposition de l’adresse aux inondations, et zones inondables connues de la commune ; une zone inondable peut entraîner des dégâts et des règles de construction.',
	mouvements_terrain:
		'Cavités souterraines et mouvements du sol, comme des glissements ou des effondrements, recensés près de l’adresse ; ils peuvent fragiliser les constructions.',
	nucleaire:
		'Zone du plan d’urgence autour d’une installation nucléaire ; une adresse dans cette zone peut recevoir des consignes de protection en cas d’accident.',
	pollution_sols:
		'Anciens sites industriels et terrains pollués recensés sur la parcelle ou à proximité ; le sol peut y contenir des substances qui demandent des précautions.',
	radon:
		'Gaz radioactif naturel qui vient du sol ; le potentiel de la commune indique s’il peut s’accumuler dans les pièces peu aérées des bâtiments.',
	remontee_nappe:
		'Eau souterraine qui peut monter près de la surface ; elle peut inonder les caves et les sous-sols.',
	risques_commune:
		'Risques naturels et technologiques recensés par l’État pour toute la commune ; ils ne disent pas si l’adresse elle-même est exposée.',
	rupture_barrage:
		'Zone qui pourrait être inondée si un barrage se rompait ; elle peut concerner des logements situés en aval du barrage.',
	seveso: 'Usine qui stocke des produits dangereux ; elle peut imposer des règles autour d’elle en cas d’accident.',
	sismicite:
		'Zone sismique fixée par décret pour toute la commune ; dans les zones les plus exposées, les constructions doivent respecter des règles parasismiques.',
	trait_de_cote:
		'Recul du littoral sous l’effet de l’érosion ; dans les communes inscrites sur la liste officielle, des règles peuvent encadrer les constructions près du rivage.',

	// --- Le prix ---
	encadrement_loyers:
		'Plafond des loyers fixé par arrêté préfectoral dans certaines villes ; s’il s’applique, le loyer d’une location est limité par un loyer de référence.',
	fiscalite:
		'Taux des impôts locaux sur les propriétés bâties (commune, intercommunalité et taxes annexes) ; il sert à calculer la taxe foncière, sans en donner le montant.',
	loyers:
		'Loyer d’annonce estimé au mètre carré pour la commune, parfois sur une zone plus large ; c’est un repère, pas le loyer d’un logement précis.',
	parcelle:
		'Terrain officiel du cadastre qui contient l’adresse, avec sa surface ; il peut porter plusieurs bâtiments et ne correspond pas forcément au seul logement.',
	prix_commune:
		'Prix médian au mètre carré des ventes de maisons et d’appartements dans la commune ; c’est un repère, pas le prix d’un logement précis.',
	urbanisme:
		'Zone du document d’urbanisme, souvent le plan local d’urbanisme (PLU), où se trouve l’adresse ; elle fixe ce qu’on peut construire ou modifier.',
	ventes_parcelle:
		'Ventes immobilières enregistrées sur la parcelle de l’adresse, avec leur date et leur prix signé ; elles donnent un historique, pas une estimation.',
	zonage_abc:
		'Classement de la commune de A bis à C selon la tension du marché du logement ; il conditionne certaines aides et certains plafonds.',
	zone_tendue:
		'Indique si la commune figure sur la liste de la taxe sur les logements vacants, qui vise les secteurs où la demande dépasse nettement l’offre.',

	// --- Le quartier ---
	antennes_5g:
		'Supports d’antennes de téléphonie mobile 5G autour de l’adresse ; ils montrent où le réseau est installé, pas la qualité du signal dans le logement.',
	delinquance:
		'Crimes et délits enregistrés par la police et la gendarmerie dans la commune, comparés au département ; ils ne mesurent pas tout ce qui se passe.',
	eau_potable:
		'Contrôles sanitaires de l’eau du robinet de la commune sur douze mois, et sa dureté ; ils disent si l’eau a respecté les limites de qualité.',
	ecoles:
		'Écoles publiques proches et collège de secteur de l’adresse ; l’indice de position sociale (IPS) décrit le milieu des élèves, pas la qualité de l’enseignement.',
	equipements:
		'Commerces, services de santé, écoles et autres équipements recensés autour de l’adresse ; ils disent ce qui est proche, pas la qualité des services.',
	fibre:
		'Indique si l’adresse peut être raccordée à la fibre optique, d’après les déclarations des opérateurs ; être raccordable ne veut pas dire être abonné.',
	grand_axe:
		'Grande route la plus proche de l’adresse ; le trafic d’un grand axe peut apporter du bruit et de la pollution de l’air aux logements proches.',
	peb: 'Zone du plan d’exposition au bruit (PEB) d’un aérodrome ; elle signale le bruit des avions et peut limiter les constructions ou imposer une meilleure isolation.',
	qualite_air:
		'Indice ATMO de qualité de l’air prévu pour la journée dans la commune ; c’est une prévision d’un jour, pas l’exposition habituelle du logement.',
	revenus:
		'Revenu du milieu des habitants de la commune, après impôts et prestations ; il donne une idée du niveau de vie local, pas d’un ménage.',
	secteur_bruit:
		'Bande le long d’une route ou d’une voie ferrée bruyante, classée par le préfet ; les constructions neuves y doivent avoir une isolation acoustique renforcée.',
	transports:
		'Arrêt de transport en commun ou gare le plus proche, à vol d’oiseau ; la distance ne dit rien du trajet ni de la fréquence.',
	voie_ferree:
		'Ligne de chemin de fer la plus proche de l’adresse ; près d’une voie, le passage des trains peut être bruyant.'
};

/** One sentence per covered field_id (commune page, comparators, DPE document, departement page). */
export const EN_CLAIR_CHAMPS = {
	// --- demographie ---
	'demographie.population':
		'Nombre d’habitants recensés dans la commune ; il donne la taille de la commune, utile pour la comparer à d’autres.',
	'demographie.nb_menages':
		'Nombre de ménages, c’est-à-dire de groupes qui vivent dans un même logement ; une personne seule compte pour un ménage.',
	'demographie.nb_logements':
		'Nombre total de logements de la commune, occupés ou non ; il additionne les résidences principales, secondaires et vacantes.',
	'demographie.nb_logements_vacants_recensement':
		'Nombre de logements déclarés vides lors du recensement ; il ne se compare pas directement au fichier des logements vacants (LOVAC), qui compte autrement.',
	'demographie.densite':
		'Nombre d’habitants par kilomètre carré, sur la surface de la commune ; une faible densité peut recouvrir un bourg dense entouré de champs.',
	'demographie.superficie':
		'Surface administrative de la commune, en kilomètres carrés, mesurée sur sa limite officielle et non en additionnant les parcelles.',
	// --- revenus ---
	'revenus.revenu_median':
		'Revenu déclaré du milieu par personne, ajusté selon la taille du foyer, avant impôts et prestations ; la moitié des habitants déclare moins, l’autre plus.',
	'revenus.taux_pauvrete':
		'Part de personnes dont le niveau de vie, après impôts et prestations, reste sous un seuil de pauvreté ; ce n’est pas le revenu déclaré.',
	'revenus.indice_gini':
		'Indice résumant les écarts entre revenus déclarés, entre 0 et 1 ; plus il monte, plus les revenus sont inégaux dans la commune.',
	'revenus.part_imposee':
		'Part des ménages fiscaux soumis à l’impôt sur le revenu, en points de pourcentage ; les autres ne paient pas cet impôt.',
	// --- zonage_abc ---
	'zonage_abc.zone':
		'Classement officiel de la tension du marché du logement, de A bis (le plus tendu) à C (le plus détendu) ; il conditionne certaines aides.',
	'zonage_abc.reclassement':
		'Indique si la commune a demandé à changer de zone ; une demande déposée n’est pas une acceptation, la zone affichée reste en vigueur.',
	// --- vacance ---
	'vacance.taux_vacance':
		'Part des logements privés vides selon le fichier des logements vacants (LOVAC), en pourcentage ; les logements sociaux ne sont pas comptés.',
	'vacance.nb_vacants_plus_2ans':
		'Nombre de logements privés vides depuis plus de deux ans selon le fichier des logements vacants (LOVAC) ; les logements sociaux ne sont pas comptés.',
	// --- fiscalite ---
	'fiscalite.taux_tfpb':
		'Taux voté pour la taxe foncière sur les propriétés bâties (TFPB), en points de pourcentage ; c’est un taux, pas un montant dû.',
	'fiscalite.taux_tfpnb':
		'Taux voté pour la taxe foncière sur les propriétés non bâties (TFPNB), en points de pourcentage ; elle vise terrains nus, agricoles ou forestiers.',
	'fiscalite.taux_th_rs':
		'Taux voté pour la taxe d’habitation sur les résidences secondaires, en points de pourcentage ; celle sur la résidence principale a été supprimée.',
	'fiscalite.taux_teom':
		'Taux voté pour la taxe d’enlèvement des ordures ménagères (TEOM), en points de pourcentage ; elle finance la collecte et le traitement des déchets.',
	'fiscalite.nom_epci':
		'Nom de l’intercommunalité (EPCI, établissement public de coopération intercommunale) de la commune ; certaines décisions se prennent à cet échelon.',
	// --- loyers ---
	'loyers.loyer_m2_appartement':
		'Loyer d’annonce estimé pour un appartement non meublé, charges comprises, en euros par mètre carré et par mois ; ce n’est pas un plafond.',
	'loyers.loyer_m2_maison':
		'Loyer d’annonce estimé pour une maison non meublée, charges comprises, en euros par mètre carré et par mois ; ce n’est pas un plafond.',
	// --- dvf ---
	'dvf.prix_m2_median':
		'Valeur du milieu des prix au mètre carré des ventes signées chez le notaire, pas des annonces ; la moitié est en dessous, l’autre au-dessus.',
	'dvf.nb_mutations_12m':
		'Nombre de ventes enregistrées dans la commune sur les douze derniers mois ; un petit nombre rend le prix du milieu moins représentatif.',
	'dvf.prix_m2_median_maison':
		'Valeur du milieu des prix au mètre carré des ventes de maisons signées chez le notaire ; les achats sur plan sont exclus.',
	'dvf.nb_mutations_12m_maison':
		'Nombre de ventes de maisons retenues pour ce prix ; s’il est trop bas, le prix n’est pas affiché, car il identifierait une vente.',
	'dvf.prix_m2_median_appartement':
		'Valeur du milieu des prix au mètre carré des ventes d’appartements signées chez le notaire ; les achats sur plan sont exclus.',
	'dvf.nb_mutations_12m_appartement':
		'Nombre de ventes d’appartements retenues pour ce prix ; s’il est trop bas, le prix n’est pas affiché, car il identifierait une vente.',
	'dvf.tendance.nb_ventes':
		'Nombre de ventes comptées chaque année ; plus il est petit, moins le prix du milieu est solide.',
	'dvf.tendance.prix_m2_median':
		'Valeur du milieu des prix au mètre carré des ventes d’une année ; elle sert à comparer les années entre elles.',
	'dvf.tendance.evolution_pct':
		'Écart entre les prix du milieu de la première et de la dernière année ; il suit les ventes, pas la valeur d’un logement.',
	'dvf.indicateurs.maison.mediane_courante':
		'Valeur du milieu des prix au mètre carré des maisons vendues récemment ; la période et le nombre de ventes sont indiqués à côté.',
	'dvf.indicateurs.maison.mediane_precedente':
		'Même calcul pour les maisons sur la période précédente, pour comparer deux périodes ; la période et le nombre de ventes sont indiqués à côté.',
	'dvf.indicateurs.appartement.mediane_courante':
		'Valeur du milieu des prix au mètre carré des appartements vendus récemment ; la période et le nombre de ventes sont indiqués à côté.',
	'dvf.indicateurs.appartement.mediane_precedente':
		'Même calcul pour les appartements sur la période précédente, pour comparer deux périodes ; la période et le nombre de ventes sont indiqués à côté.',
	'dvf.indicateurs.maison.variation_pct':
		'Évolution en pourcentage entre les deux périodes comparées pour les maisons ; zéro veut dire que la valeur du milieu n’a pas bougé.',
	'dvf.indicateurs.appartement.variation_pct':
		'Évolution en pourcentage entre les deux périodes comparées pour les appartements ; zéro veut dire que la valeur du milieu n’a pas bougé.',
	// --- dpe ---
	'dpe.indicateurs.part_fg_pct':
		'Part des diagnostics de performance énergétique (DPE) classés F ou G, parmi ceux à classe connue ; elle ne décrit pas tout le parc.',
	'dpe.dpe_passoires_pct':
		'Part des diagnostics de performance énergétique (DPE) classés F ou G, les plus énergivores ; un DPE est un document, pas tout le parc.',
	'dpe.dpe_median':
		'Classe énergie du milieu des diagnostics de performance énergétique (DPE) dont la classe est connue, de A très performant à G très énergivore.',
	'dpe.annee_construction_mediane':
		'Année du milieu de construction des logements ayant un diagnostic de performance énergétique (DPE) ; ce n’est pas tout le bâti de la commune.',
	// --- equipements ---
	'equipements.nb_equipements':
		'Somme des effectifs d’équipements publiés pour la commune ; c’est un compte observé, qui ne prouve pas que la liste est complète.',
	// --- scolaire ---
	'scolaire.nb_etablissements_scolaires':
		'Nombre d’établissements scolaires distincts référencés pour les secteurs de la commune ; ce n’est pas un recensement complet des écoles.',
	// --- monuments ---
	'monuments.nb_monuments':
		'Nombre de notices distinctes du patrimoine recensé dans la commune ; une notice ne compte pas forcément un seul objet physique.',
	// --- foncier_pm ---
	'foncier_pm.nb_proprietaires_pm':
		'Nombre de propriétaires distincts qui sont des personnes morales, comme des sociétés ou des associations ; le chiffre peut être incomplet.',
	'foncier_pm.surface_pm_ha':
		'Surface totale des parcelles distinctes détenues par ces personnes morales, en hectares ; une parcelle n’est comptée qu’une fois, le total peut être incomplet.',
	// --- connectivite ---
	'connectivite.fibre_pct':
		'Part des immeubles avec une technologie fibre optique, en points de pourcentage ; ce taux compte des immeubles, pas des adresses ni des logements.',
	// --- dpe ---
	'bien.dpe.etiquette_dpe':
		'Classe énergie du logement, de A (très performant) à G (très énergivore), issue de son DPE (diagnostic de performance énergétique).',
	'bien.dpe.etiquette_ges':
		'Classe d’émissions de gaz à effet de serre (GES) du logement, de A à G ; elle est distincte de la classe énergie.',
	'bien.dpe.surface_habitable':
		'Surface habitable du logement retenue par le diagnostic, en mètres carrés ; elle peut différer de la surface indiquée dans un acte de vente.',
	'bien.dpe.annee_construction':
		'Année de construction du logement, telle que déclarée dans son diagnostic ; elle manque parfois, et seule une tranche de période est alors connue.',
	'bien.dpe.periode_construction':
		'Tranche de période de construction du logement, utilisée quand l’année exacte manque ; elle suit les grandes étapes des règles de construction thermique.',
	'bien.dpe.type_batiment':
		'Nature du bâtiment diagnostiqué, maison individuelle, appartement ou immeuble collectif ; elle aide à savoir quel type de logement est décrit.',
	'bien.dpe.cout_total_5_usages':
		'Dépense d’énergie annuelle estimée du logement, en euros par an, pour cinq usages comme le chauffage ; c’est une simulation, pas une facture constatée.',
	'bien.dpe.energie_chauffage':
		'Énergie principale qui chauffe le logement, électricité, gaz, fioul, bois ou réseau de chaleur, telle que relevée par le diagnostic.',
	'bien.dpe.isolation':
		'Avis du diagnostic sur l’isolation des murs, de la toiture, du plancher bas et des fenêtres ; c’est une appréciation, pas une mesure.',
	'bien.dpe.conso_5_usages_ep':
		'Consommation annuelle d’énergie primaire estimée du logement, en kilowattheures par an ; elle inclut les pertes de production et n’est pas une facture.',
	// --- dvf ---
	'bien.dvf.valeur_fonciere':
		'Prix total de la dernière vente connue, en euros ; il peut couvrir plusieurs lots, comme un appartement et son garage.',
	'bien.dvf.prix_m2':
		'Prix au mètre carré de la dernière vente connue, rapporté à la surface bâtie déclarée ; c’est un prix passé, pas une estimation actuelle.',
	'bien.dvf.date_mutation':
		'Date de la dernière vente connue ; les ventes arrivent avec quelques mois de retard, une vente très récente peut manquer.',
	'bien.dvf.type_local':
		'Nature du local vendu lors de la dernière vente, maison, appartement, dépendance ou local d’activité ; cela dit ce qui a été acheté.',
	'bien.dvf.surface_reelle_bati':
		'Surface bâtie déclarée dans l’acte de la dernière vente, en mètres carrés ; elle peut différer de la surface relevée par un diagnostic.',
	'bien.dvf.nombre_pieces_principales':
		'Nombre de pièces principales déclarées dans l’acte, chambres et séjour ; la cuisine, la salle de bains et les couloirs ne sont pas comptés.',
	'bien.dvf.classe_mutation':
		'Ce que couvre la vente, logement seul, plusieurs logements, mélange logement et local commercial, terrain ; un prix global ne se divise pas.',
	'bien.dvf.surface_bati_totale':
		'Surface bâtie totale de la vente, additionnée sur tous ses locaux ; si elle en porte plusieurs, ce n’est pas la surface d’un seul logement.',
	// --- cadastre ---
	'bien.cadastre.contenance':
		'Surface de la parcelle cadastrale, en mètres carrés ; c’est le terrain où se trouve le bien, pas la surface habitable de la construction.',
	// --- risques ---
	'bien.risques.inondation':
		'Indique si l’inondation figure parmi les risques recensés pour la commune ; cela ne prouve pas que le bien est inondable.',
	'bien.risques.sismique':
		'Indique si le séisme figure parmi les risques de la commune ; cela ne dit ni le niveau de sismicité ni la solidité du bâtiment.',
	'bien.risques.expo_argiles':
		'Exposition du point consulté au retrait-gonflement des argiles, des sols qui gonflent et se rétractent avec l’humidité ; ce n’est pas une étude de sol.',
	// --- urbanisme ---
	'bien.urbanisme.zonage_libelle':
		'Nom de la zone du plan local d’urbanisme (PLU) au point consulté ; ce zonage encadre les constructions, mais une parcelle peut recouper plusieurs zones.',
	'bien.urbanisme.type_zone':
		'Grande catégorie de la zone d’urbanisme au point consulté, urbaine, à urbaniser, agricole ou naturelle ; elle ne décrit pas toute la parcelle.',
	// --- loyers ---
	'loyers.prediction.loyer_m2':
		'Loyer d’annonce estimé pour ce type d’habitat non meublé, en euros par mètre carré et par mois, charges comprises ; ce n’est pas un plafond.',
	'loyers.prediction.loyer_m2_bas':
		'Borne basse du loyer estimé, en euros par mètre carré et par mois ; elle décrit l’incertitude du modèle, pas la dispersion des loyers.',
	'loyers.prediction.loyer_m2_haut':
		'Borne haute du loyer estimé, en euros par mètre carré et par mois ; elle décrit l’incertitude du modèle, pas la dispersion des loyers.',
	'loyers.prediction.r2_ajuste':
		'Score d’ajustement du modèle (R² ajusté), sans unité ; plus il est proche de 1, mieux le modèle reproduit les loyers observés.',
	'loyers.prediction.r2':
		'Ancien nom du score d’ajustement du modèle, gardé pour compatibilité ; même valeur, sans unité, qui ne se lit pas comme une probabilité.',
	'loyers.prediction.nb_observations_commune':
		'Nombre d’annonces de location observées dans la commune ; zéro veut dire qu’aucune n’a été vue, et l’estimation vient d’un territoire plus large.',
	'loyers.prediction.nb_observations_maille':
		'Nombre d’annonces observées dans le territoire d’estimation, qui regroupe plusieurs communes ; il ne s’ajoute pas à celui de la commune.',
	'loyers.prediction.nb_observations':
		'Ancien nom du nombre d’annonces observées dans la commune, gardé pour compatibilité ; même valeur que le champ actuel.',
	'loyers.prediction.trimestre_reference':
		'Trimestre de l’année auquel se rapportent les annonces, de 1 à 4 ; il situe l’estimation dans l’année, sans montrer une évolution.',
	'loyers.prediction.surface_reference_m2':
		'Surface habitable du logement de référence, en mètres carrés ; un logement plus grand ou plus petit peut avoir un autre prix au mètre carré.',
	'loyers.prediction.surface_piece_reference_m2':
		'Surface moyenne par pièce du logement type de référence, en mètres carrés ; elle décrit la taille de référence utilisée pour l’estimation.',
	// --- dvf ---
	'dvf.indicateurs.effectif':
		'Nombre de ventes de maisons ou d’appartements retenues pour ce calcul ; un petit nombre rend la valeur du milieu peu représentative.',
	'dvf.indicateurs.quantiles.p10':
		'Prix au mètre carré sous lequel se situe un dixième des ventes de la catégorie ; publié seulement quand le calcul est validé.',
	'dvf.indicateurs.quantiles.p50':
		'Valeur du milieu des prix au mètre carré de la catégorie sur la période récente ; publié seulement si le calcul est validé.',
	'dvf.indicateurs.quantiles.p90':
		'Prix au mètre carré sous lequel se situent neuf dixièmes des ventes de la catégorie ; publié seulement quand le calcul est validé.',
	// --- dpe ---
	'dpe.indicateurs.population.total':
		'Nombre de diagnostics de performance énergétique (DPE) de la commune, sans ceux que la source a supprimés ; ce n’est pas le nombre de logements.',
	'dpe.indicateurs.population.classes_connues':
		'Nombre de diagnostics de performance énergétique (DPE) de la commune dont la classe énergie est connue ; c’est la base des parts calculées par classe.',
	'dpe.indicateurs.population.types_batiment':
		'Répartition des diagnostics de performance énergétique (DPE) selon le bâtiment déclaré, maison, appartement, autre ou inconnu ; ces nombres font le total.',
	'dpe.indicateurs.population.validite':
		'Nombre de diagnostics de performance énergétique (DPE) selon leur validité à la date d’évaluation, comme encore valide ou expiré ; ces nombres font le total.',
	'dpe.indicateurs.population.remplacement':
		'Nombre de diagnostics de performance énergétique (DPE) remplacés par un plus récent, ou non ; les cas ambigus ou non vérifiés sont comptés à part.',
	'dpe.indicateurs.population.regimes':
		'Nombre de DPE (diagnostic de performance énergétique) par jeu de données ; un même DPE peut compter dans deux jeux, ces nombres ne s’additionnent pas.',
	'dpe.indicateurs.population.source_inconnue':
		'Nombre de DPE (diagnostic de performance énergétique) dont l’état dans la source publique n’est pas connu ; ce compte limite la lecture des autres chiffres.',
	'dpe.indicateurs.population.revision_non_verifiee':
		'Nombre de DPE (diagnostic de performance énergétique) dont la version la plus récente n’a pas été vérifiée ; aujourd’hui ce nombre égale le total.',
	'dpe.indicateurs.distribution.classes':
		'Nombre de DPE (diagnostic de performance énergétique) de la commune dans chaque classe énergie, de A très performant à G très énergivore.',
	'dpe.indicateurs.distribution.inconnue':
		'Nombre de DPE (diagnostic de performance énergétique) de la commune sans classe énergie connue ; ces documents n’entrent dans aucune classe de A à G.',
	'dpe.indicateurs.dates_etablissement_inconnues':
		'Nombre de DPE (diagnostic de performance énergétique) dont la date d’établissement n’est pas connue ; cette date indique quelle méthode de calcul a servi.',
	'dpe.indicateurs.modes':
		'Classe énergie la plus fréquente parmi les DPE (diagnostic de performance énergétique) à classe connue ; en cas d’égalité, toutes sont données.',
	'dpe.indicateurs.mediane_ordinale':
		'Classe énergie du milieu parmi les DPE (diagnostic de performance énergétique) à classe connue, de A à G ; absente si aucune n’est connue.',
	// --- adresse ---
	'adresse.dpe.nombre_declares':
		'Nombre de DPE (diagnostic de performance énergétique) déclarés avec cette adresse saisie ; aucun n’est rattaché à un logement précis.',
	'adresse.dpe.plus_recent':
		'DPE (diagnostic de performance énergétique) le plus récent déclaré à cette adresse ; il n’est rattaché à aucun logement précis.',
	'adresse.dpe.constat_d110':
		'Constat donné quand tout concorde, une maison, un seul bâtiment et un seul DPE (diagnostic de performance énergétique) ; il ne prouve pas l’identité d’un logement.',
	'adresse.dpe.documents_adresse_brute':
		'Nombre de DPE (diagnostic de performance énergétique) dont l’adresse saisie correspond exactement à cette adresse ; ce n’est pas une identité de logement.',
	// --- dpe ---
	'dpe.document.etiquette_dpe':
		'Classe énergie, de A très performant à G très énergivore, déclarée par ce DPE (diagnostic de performance énergétique) ; elle décrit ce qui est déclaré.',
	'dpe.document.etiquette_ges':
		'Classe climat, de A peu émettrice à G très émettrice, déclarée par ce DPE (diagnostic de performance énergétique) ; elle se lit à part.',
	'dpe.document.surface_habitable':
		'Surface habitable déclarée par ce DPE (diagnostic de performance énergétique), en mètres carrés, telle que saisie par le diagnostiqueur.',
	'dpe.document.adresse_declaree':
		'Adresse saisie par le diagnostiqueur, si elle diffère de l’adresse retenue ; elle n’est pas affichée quand elle porte un complément.',
	'dpe.document.conso_ep':
		'Consommation annuelle d’énergie primaire déclarée par ce DPE (diagnostic de performance énergétique), en kilowattheures par an ; c’est une estimation, pas une facture.',
	'dpe.document.conso_ep_m2':
		'Consommation d’énergie primaire par mètre carré et par an, publiée pour ce DPE (diagnostic de performance énergétique) ; elle n’est jamais recalculée.',
	'dpe.document.emissions_ges_m2':
		'Émissions de gaz à effet de serre par mètre carré et par an, publiées pour ce DPE (diagnostic de performance énergétique) ; jamais recalculées.',
	'dpe.document.emissions_ges':
		'Gaz à effet de serre émis par an selon ce DPE (diagnostic de performance énergétique), en kilogrammes d’équivalent dioxyde de carbone ; c’est une estimation standardisée.',
	'dpe.document.cout_annuel':
		'Dépense d’énergie annuelle estimée déclarée par ce DPE (diagnostic de performance énergétique), en euros par an ; c’est une simulation, pas une facture constatée.',
	'dpe.document.type_batiment':
		'Nature du bâtiment décrit par ce DPE (diagnostic de performance énergétique), maison, appartement ou immeuble collectif, telle que déclarée par le diagnostiqueur.',
	'dpe.document.periode_construction':
		'Tranche de période de construction déclarée par ce DPE (diagnostic de performance énergétique) ; elle suit les grandes étapes des règles de construction thermique.',
	'dpe.document.energie_chauffage':
		'Énergie principale de chauffage déclarée par ce DPE (diagnostic de performance énergétique), comme le gaz, le fioul ou le bois.',
	'dpe.document.installation_chauffage':
		'Type d’installation de chauffage déclaré par ce DPE (diagnostic de performance énergétique), individuelle, collective ou mixte.',
	'dpe.document.isolation':
		'Avis porté par ce DPE (diagnostic de performance énergétique) sur l’isolation de l’enveloppe, murs, toiture, plancher bas et ouvertures ; c’est une appréciation.',
	'dpe.document.date_etablissement':
		'Date à laquelle le diagnostiqueur a établi ce DPE (diagnostic de performance énergétique) ; elle indique la méthode de calcul utilisée.',
	'dpe.document.date_fin_validite':
		'Date de fin de validité déclarée par ce DPE (diagnostic de performance énergétique) ; elle n’est jamais calculée, et un document dépassé reste listé.',
	'dpe.document.methode':
		'Méthode de calcul de ce DPE (diagnostic de performance énergétique), ancienne ou actuelle selon sa date ; deux méthodes différentes ne se comparent pas.',
	'dpe.document.adresse_ban':
		'Adresse de la Base Adresse Nationale (BAN) déclarée pour ce DPE (diagnostic de performance énergétique), reprise sans vérification ; elle ne désigne aucun logement.',
	// --- adresse ---
	'adresse.dvf.ventes_parcelle.nombre':
		'Nombre de ventes sur la parcelle cadastrale du point de l’adresse, sur toute la période importée ; une vente de plusieurs parcelles compte une fois.',
	'adresse.dvf.ventes_parcelle.derniere':
		'Date de la vente la plus récente enregistrée sur la parcelle cadastrale où se trouve le point de l’adresse.',
	'adresse.risques.argiles_au_point':
		'Exposition du point de l’adresse au retrait-gonflement des argiles, le jour de la consultation ; elle ne dit rien des autres risques.',
	'adresse.reference_commune.prix_median_maison':
		'Valeur du milieu des prix au mètre carré des maisons vendues dans la commune, en référence ; ce n’est pas une estimation de l’adresse.',
	'adresse.reference_commune.prix_median_appartement':
		'Valeur du milieu des prix au mètre carré des appartements vendus dans la commune, en référence ; ce n’est pas une estimation de l’adresse.',
	'adresse.dvf.vente.prix':
		'Prix total de la vente, qui peut porter sur plusieurs locaux ; il n’est rattaché à aucun logement précis.',
	'adresse.dvf.vente.prix_m2':
		'Prix total divisé par la surface bâtie, calculé seulement pour la vente d’un seul logement ; ce n’est pas une estimation de l’adresse.',
	'adresse.dvf.vente.surface_batie':
		'Surface bâtie réelle déclarée pour une vente, somme de tous ses locaux ; elle ne décrit aucun logement précis.',
	'adresse.dvf.ventes_ressemblantes.effectif':
		'Nombre de ventes d’une seule maison ou d’un seul appartement comparables, selon les critères affichés ; aucune n’est rattachée à un logement.',
	'adresse.dvf.ventes_adresse.nombre':
		'Nombre de ventes rattachées à l’adresse saisie dans l’acte ; zéro ne veut pas dire aucune vente, car une partie ne retrouve pas son adresse.',
	'adresse.batiments.nombre_parcelle':
		'Nombre de bâtiments recensés sur la parcelle du point de l’adresse, annexes comprises ; il ne désigne aucun bâtiment précis de l’adresse.',
	'adresse.batiments.nombre_parcelle_hors_annexe':
		'Nombre de bâtiments de la parcelle, sans les annexes ; un bâtiment dont l’usage est inconnu reste compté.',
	'adresse.batiment.hauteur':
		'Hauteur en mètres du bâtiment qui contient le point de l’adresse ; donnée seulement quand un seul bâtiment contient ce point.',
	'adresse.batiment.etages':
		'Nombre d’étages du bâtiment qui contient le point de l’adresse ; donné seulement quand un seul bâtiment contient ce point.',
	'adresse.parcelle.contenance':
		'Surface cadastrale, en mètres carrés, de la parcelle où se trouve le point de l’adresse ; c’est le terrain, pas la surface habitable.',
	'adresse.copropriete.immatriculation':
		'Nombre de copropriétés immatriculées sur la parcelle de l’adresse ; aucun lot ni logement n’en est déduit.',
	'adresse.risques.liste_commune':
		'Risques naturels et technologiques recensés pour la commune de l’adresse le jour de la consultation ; c’est une référence communale, pas une exposition de l’adresse.',
	'adresse.risques.radon_commune':
		'Catégorie du potentiel radon de la commune, de 1 faible à 3 significatif ; c’est une référence communale, pas une mesure à l’adresse.',
	'adresse.risques.icpe_commune':
		'Nombre d’installations classées pour la protection de l’environnement (ICPE) dans la commune ; c’est une référence communale, pas une distance.',
	'adresse.urbanisme.zone_plu':
		'Zone du document d’urbanisme, plan local d’urbanisme ou carte communale, qui contient le point de l’adresse, le jour de la consultation.',
	'adresse.urbanisme.prescriptions':
		'Règles du document d’urbanisme qui s’appliquent au point de l’adresse, par exemple un espace boisé classé, le jour de la consultation.',
	'adresse.connectivite.eligibilite_fibre':
		'Éligibilité à la fibre de l’immeuble, si un seul immeuble porte ce numéro et cette voie ; elle ne dit rien d’un abonnement.',
	'adresse.connectivite.debit':
		'Classe de débit descendant publiée pour la fibre de cet immeuble ; elle décrit le réseau, pas un abonnement précis.',
	'adresse.transports.arret_proche':
		'Arrêts de transport et gares proches, à moins d’un kilomètre, avec leur distance à vol d’oiseau ; ce n’est pas un temps de trajet.',
	// --- departement ---
	'departement.population.total':
		'Nombre d’habitants du département, somme des populations des communes chargées ; si peu de communes sont comptées, le total est partiel.',
	'departement.population.communes_comptees':
		'Nombre de communes dont la population entre dans le total du département, pour savoir sur combien de communes il repose.',
	'departement.prix.appartement.mediane_m2':
		'Valeur du milieu des prix au mètre carré des appartements du département sur la période récente ; pas une moyenne des communes.',
	'departement.prix.appartement.effectif':
		'Nombre de ventes d’appartements retenues pour ce prix sur la période récente ; peu de ventes rend le prix moins représentatif.',
	'departement.prix.maison.mediane_m2':
		'Valeur du milieu des prix au mètre carré des maisons du département sur la période récente ; pas une moyenne des communes.',
	'departement.prix.maison.effectif':
		'Nombre de ventes de maisons retenues pour ce prix sur la période récente ; peu de ventes rend le prix moins représentatif.',
	'departement.dpe.part_fg_pct':
		'Part des DPE (diagnostic de performance énergétique) classés F ou G ; ce sont des documents, pas une part des logements du département.',
	'departement.dpe.classes_connues':
		'Nombre de DPE (diagnostic de performance énergétique) du département à classe connue ; c’est la base de la part des classes F et G.'
} satisfies Record<CoveredFieldId, string>;

/** « En clair » of a report theme; null for a theme without a sentence (the test forbids it). */
export function enClairTheme(id: string): string | null {
	return Object.hasOwn(EN_CLAIR_THEMES, id) ? EN_CLAIR_THEMES[id] : null;
}

/** « En clair » of a field_id; null for a field without one (identifiers, millésimes). */
export function enClairChamp(fieldId: string | null | undefined): string | null {
	if (fieldId == null || !Object.hasOwn(EN_CLAIR_CHAMPS, fieldId)) return null;
	return (EN_CLAIR_CHAMPS as Record<string, string>)[fieldId];
}

// ---- Technical flags of a read ------------------------------------------------------------------

/**
 * Flags that carry a reserve only in one direction. `quand` is the value that is worth saying; the other
 * value says nothing to the reader and is not shown (« Liste incomplète : non » was a double negative).
 */
export const RESERVES_LECTURE: Readonly<Record<string, { quand: boolean; libelle: string }>> = {
	liste_incomplete: { quand: true, libelle: 'Liste incomplète' },
	lecture_complete: { quand: false, libelle: 'Lecture incomplète' },
	reseaux_complets: { quand: false, libelle: 'Liste des réseaux incomplète' },
	plus_proche_connue: { quand: true, libelle: 'Gare la plus proche connue' }
};

/** Flags whose « non » says nothing (the rest of the detail already says it): shown only when true. */
export const DRAPEAUX_MUETS_SI_FAUX: ReadonlySet<string> = new Set(['un_seul_batiment_etabli', 'lue_sur_la_commune', 'diffuse']);

const LECTURE_PARTIELLE = 'La source n’a renvoyé qu’une partie de la liste ; d’autres éléments peuvent exister.';
const RAYON_HORS_COUVERTURE =
	'Le rayon déborde sur un département que DataCommune ne couvre pas encore ; d’autres éléments peuvent s’y trouver.';

/** Why a list is incomplete, by theme: the reasons differ (page bound, radius, list cut for display). */
const RAISONS_LISTE_INCOMPLETE: Readonly<Record<string, string>> = {
	catnat: LECTURE_PARTIELLE,
	seveso: LECTURE_PARTIELLE,
	grand_axe: LECTURE_PARTIELLE,
	voie_ferree: LECTURE_PARTIELLE,
	mouvements_terrain: LECTURE_PARTIELLE,
	urbanisme: LECTURE_PARTIELLE,
	icpe_commune: LECTURE_PARTIELLE,
	risques_commune: LECTURE_PARTIELLE,
	antennes_5g: RAYON_HORS_COUVERTURE,
	equipements: RAYON_HORS_COUVERTURE,
	transports:
		'Le rayon déborde sur un département non couvert, une catégorie d’arrêts n’est pas importée ou la liste est écourtée ; d’autres arrêts peuvent exister.',
	construction: 'Seule une partie des groupes de bâtiments de la parcelle est listée ici.',
	copropriete: 'Seule une partie des copropriétés de la parcelle est listée ici.'
};

const RAISONS_RESERVES: Readonly<Record<string, string>> = {
	lecture_complete: 'La source n’a renvoyé qu’une partie des prélèvements ; les chiffres ne portent que sur ceux qui ont été lus.',
	reseaux_complets: 'La liste des réseaux d’eau de la commune n’a pas pu être lue en entier.',
	plus_proche_connue: 'Une gare plus proche peut exister dans un département que DataCommune ne couvre pas encore.'
};

/** Reason of the similar sales' « Liste incomplète » (address page, C16). */
export const RAISON_COMPARABLES_INCOMPLETS =
	'Le rayon déborde sur un département que DataCommune ne couvre pas encore ; des ventes ressemblantes peuvent s’y trouver.';

/** Why a reserve flag is raised; a theme without its own reason gets the general one. */
export function raisonReserve(cle: string, themeId: string | null): string {
	if (cle === 'liste_incomplete') {
		return (themeId && RAISONS_LISTE_INCOMPLETE[themeId]) || 'La source n’a pas permis de tout lister ; d’autres éléments peuvent exister.';
	}
	return RAISONS_RESERVES[cle] ?? LECTURE_PARTIELLE;
}

/** Number of words of a sentence, as the owner counts them (an elided « l’adresse » is one word). */
export function nombreDeMots(phrase: string): number {
	return phrase.match(/[\p{L}\p{N}][\p{L}\p{N}’'-]*/gu)?.length ?? 0;
}
