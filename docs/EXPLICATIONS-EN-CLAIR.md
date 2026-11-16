# Explications « En clair » — tableau de relecture

Retour d’Ianis du 09/11 : « l’explication la plus simple et rapide possible de chaque chose ». Chaque phrase s’affiche **en tête** du détail (critère de la fiche adresse, volet ⓘ de la fiche commune, des comparateurs, du document DPE et du département), avant la définition, la méthode et la source.

- **Source unique** : `frontend/src/lib/data/explications-simples.ts`. Ce tableau en est une copie générée le 2026-11-09 pour la relecture ; une correction se fait dans le module.
- **Règle** : au plus 25 mots, aucun texte interdit des surfaces publiques (« passoire », « maille », « ce bien »…), ce que c’est puis pourquoi ça compte pour un logement, sans promesse ni jugement, sigle développé à côté de lui. Contrôlée par `explications-simples.test.ts`, qui échoue aussi si un thème du registre ou un champ couvert n’a pas sa phrase.
- **Rédaction** : premier jet par lots, relu phrase par phrase contre la définition publiée (`explications.ts`) ou la docstring du thème ; une trentaine réécrites à la relecture. Seveso reprend mot pour mot l’exemple d’Ianis.

## 1. Critères de la fiche adresse (43)

### Le bâti et l’énergie

| Critère | Explication | Mots |
|---|---|---|
| Bâtiment (`batiment`) | Bâtiment situé au point de l’adresse (hauteur, étages) et bâtiments de sa parcelle ; cela aide à savoir de quel bâtiment il s’agit. | 22 |
| Construction (`construction`) | Année de construction des bâtiments de la parcelle ; dans les bâtiments anciens, des matériaux comme les peintures au plomb peuvent être présents. | 22 |
| Copropriété (`copropriete`) | Copropriétés inscrites au registre national sur la parcelle de l’adresse ; elles signalent un immeuble partagé, avec un syndic, des charges et des règles communes. | 24 |
| DPE (`dpe`) | Diagnostics de performance énergétique (DPE) publiés à l’adresse ; leur classe, de A à G, indique la consommation d’énergie estimée du logement. | 21 |

### Le terrain et les risques

| Critère | Explication | Mots |
|---|---|---|
| Altitude (`altitude`) | Hauteur du sol à l’adresse au-dessus du niveau de la mer ; elle aide à situer le relief mais ne dit rien, seule, d’un risque. | 24 |
| Argiles (`argiles`) | Sol argileux qui gonfle quand il est humide et se rétracte quand il sèche ; ces mouvements peuvent fissurer les murs d’une maison. | 22 |
| Catastrophes naturelles (`catnat`) | Arrêtés qui reconnaissent une catastrophe naturelle survenue dans la commune ; ils montrent des événements passés, qui peuvent se reproduire. | 19 |
| Cavités et mouvements de terrain (`mouvements_terrain`) | Cavités souterraines et mouvements du sol, comme des glissements ou des effondrements, recensés près de l’adresse ; ils peuvent fragiliser les constructions. | 21 |
| Débroussaillement (`debroussaillement`) | Obligation légale de débroussailler autour des constructions dans certaines zones, pour limiter les incendies ; elle peut s’appliquer à la parcelle de l’adresse. | 22 |
| Géologie (`geologie`) | Roche dominante du secteur, lue sur une carte nationale peu détaillée ; elle décrit le sous-sol à grande échelle, pas le terrain exact de l’adresse. | 24 |
| Inondation (`inondation`) | Exposition de l’adresse aux inondations, et zones inondables connues de la commune ; une zone inondable peut entraîner des dégâts et des règles de construction. | 24 |
| Installations classées (`icpe_commune`) | Installations classées pour la protection de l’environnement (ICPE), comme des usines ou des élevages, comptées dans toute la commune ; elles peuvent causer des nuisances. | 24 |
| Nucléaire (`nucleaire`) | Zone du plan d’urgence autour d’une installation nucléaire ; une adresse dans cette zone peut recevoir des consignes de protection en cas d’accident. | 22 |
| Radon (`radon`) | Gaz radioactif naturel qui vient du sol ; le potentiel de la commune indique s’il peut s’accumuler dans les pièces peu aérées des bâtiments. | 23 |
| Remontée de nappe (`remontee_nappe`) | Eau souterraine qui peut monter près de la surface ; elle peut inonder les caves et les sous-sols. | 17 |
| Risques recensés (`risques_commune`) | Risques naturels et technologiques recensés par l’État pour toute la commune ; ils ne disent pas si l’adresse elle-même est exposée. | 20 |
| Rupture de barrage (`rupture_barrage`) | Zone qui pourrait être inondée si un barrage se rompait ; elle peut concerner des logements situés en aval du barrage. | 20 |
| Sismicité (`sismicite`) | Zone sismique fixée par décret pour toute la commune ; dans les zones les plus exposées, les constructions doivent respecter des règles parasismiques. | 22 |
| Sites Seveso (`seveso`) | Usine qui stocke des produits dangereux ; elle peut imposer des règles autour d’elle en cas d’accident. | 16 |
| Sols pollués (`pollution_sols`) | Anciens sites industriels et terrains pollués recensés sur la parcelle ou à proximité ; le sol peut y contenir des substances qui demandent des précautions. | 24 |
| Trait de côte (`trait_de_cote`) | Recul du littoral sous l’effet de l’érosion ; dans les communes inscrites sur la liste officielle, des règles peuvent encadrer les constructions près du rivage. | 24 |

### Le prix

| Critère | Explication | Mots |
|---|---|---|
| Encadrement des loyers (`encadrement_loyers`) | Plafond des loyers fixé par arrêté préfectoral dans certaines villes ; s’il s’applique, le loyer d’une location est limité par un loyer de référence. | 23 |
| Loyers (`loyers`) | Loyer d’annonce estimé au mètre carré pour la commune, parfois sur une zone plus large ; c’est un repère, pas le loyer d’un logement précis. | 24 |
| Parcelle (`parcelle`) | Terrain officiel du cadastre qui contient l’adresse, avec sa surface ; il peut porter plusieurs bâtiments et ne correspond pas forcément au seul logement. | 23 |
| Prix au m² (`prix_commune`) | Prix médian au mètre carré des ventes de maisons et d’appartements dans la commune ; c’est un repère, pas le prix d’un logement précis. | 23 |
| Taxe foncière (`fiscalite`) | Taux des impôts locaux sur les propriétés bâties (commune, intercommunalité et taxes annexes) ; il sert à calculer la taxe foncière, sans en donner le montant. | 25 |
| Urbanisme (`urbanisme`) | Zone du document d’urbanisme, souvent le plan local d’urbanisme (PLU), où se trouve l’adresse ; elle fixe ce qu’on peut construire ou modifier. | 22 |
| Ventes de la parcelle (`ventes_parcelle`) | Ventes immobilières enregistrées sur la parcelle de l’adresse, avec leur date et leur prix signé ; elles donnent un historique, pas une estimation. | 22 |
| Zonage ABC (`zonage_abc`) | Classement de la commune de A bis à C selon la tension du marché du logement ; il conditionne certaines aides et certains plafonds. | 23 |
| Zone tendue (`zone_tendue`) | Indique si la commune figure sur la liste de la taxe sur les logements vacants, qui vise les secteurs où la demande dépasse nettement l’offre. | 25 |

### Le quartier

| Critère | Explication | Mots |
|---|---|---|
| Bruit des avions (`peb`) | Zone du plan d’exposition au bruit (PEB) d’un aérodrome ; elle signale le bruit des avions et peut limiter les constructions ou imposer une meilleure isolation. | 25 |
| Délinquance enregistrée (`delinquance`) | Crimes et délits enregistrés par la police et la gendarmerie dans la commune, comparés au département ; ils ne mesurent pas tout ce qui se passe. | 25 |
| Eau du robinet (`eau_potable`) | Contrôles sanitaires de l’eau du robinet de la commune sur douze mois, et sa dureté ; ils disent si l’eau a respecté les limites de qualité. | 25 |
| Écoles publiques (`ecoles`) | Écoles publiques proches et collège de secteur de l’adresse ; l’indice de position sociale (IPS) décrit le milieu des élèves, pas la qualité de l’enseignement. | 24 |
| Équipements (`equipements`) | Commerces, services de santé, écoles et autres équipements recensés autour de l’adresse ; ils disent ce qui est proche, pas la qualité des services. | 23 |
| Fibre (`fibre`) | Indique si l’adresse peut être raccordée à la fibre optique, d’après les déclarations des opérateurs ; être raccordable ne veut pas dire être abonné. | 23 |
| Grands axes routiers (`grand_axe`) | Grande route la plus proche de l’adresse ; le trafic d’un grand axe peut apporter du bruit et de la pollution de l’air aux logements proches. | 25 |
| Qualité de l'air (`qualite_air`) | Indice ATMO de qualité de l’air prévu pour la journée dans la commune ; c’est une prévision d’un jour, pas l’exposition habituelle du logement. | 23 |
| Réseau mobile 5G (`antennes_5g`) | Supports d’antennes de téléphonie mobile 5G autour de l’adresse ; ils montrent où le réseau est installé, pas la qualité du signal dans le logement. | 24 |
| Revenus (`revenus`) | Revenu du milieu des habitants de la commune, après impôts et prestations ; il donne une idée du niveau de vie local, pas d’un ménage. | 24 |
| Secteur affecté par le bruit (`secteur_bruit`) | Bande le long d’une route ou d’une voie ferrée bruyante, classée par le préfet ; les constructions neuves y doivent avoir une isolation acoustique renforcée. | 24 |
| Transports (`transports`) | Arrêt de transport en commun ou gare le plus proche, à vol d’oiseau ; la distance ne dit rien du trajet ni de la fréquence. | 24 |
| Voies ferrées (`voie_ferree`) | Ligne de chemin de fer la plus proche de l’adresse ; près d’une voie, le passage des trains peut être bruyant. | 20 |

## 2. Valeurs des volets ⓘ (152 champs)

Fiche commune, comparateurs de communes et d’adresses, page du document DPE, page département. Repérés par leur identifiant de champ (`field_id`).

### demographie

| Champ | Explication | Mots |
|---|---|---|
| `demographie.population` | Nombre d’habitants recensés dans la commune ; il donne la taille de la commune, utile pour la comparer à d’autres. | 19 |
| `demographie.nb_menages` | Nombre de ménages, c’est-à-dire de groupes qui vivent dans un même logement ; une personne seule compte pour un ménage. | 19 |
| `demographie.nb_logements` | Nombre total de logements de la commune, occupés ou non ; il additionne les résidences principales, secondaires et vacantes. | 18 |
| `demographie.nb_logements_vacants_recensement` | Nombre de logements déclarés vides lors du recensement ; il ne se compare pas directement au fichier des logements vacants (LOVAC), qui compte autrement. | 23 |
| `demographie.densite` | Nombre d’habitants par kilomètre carré, sur la surface de la commune ; une faible densité peut recouvrir un bourg dense entouré de champs. | 22 |
| `demographie.superficie` | Surface administrative de la commune, en kilomètres carrés, mesurée sur sa limite officielle et non en additionnant les parcelles. | 19 |

### revenus

| Champ | Explication | Mots |
|---|---|---|
| `revenus.revenu_median` | Revenu déclaré du milieu par personne, ajusté selon la taille du foyer, avant impôts et prestations ; la moitié des habitants déclare moins, l’autre plus. | 24 |
| `revenus.taux_pauvrete` | Part de personnes dont le niveau de vie, après impôts et prestations, reste sous un seuil de pauvreté ; ce n’est pas le revenu déclaré. | 24 |
| `revenus.indice_gini` | Indice résumant les écarts entre revenus déclarés, entre 0 et 1 ; plus il monte, plus les revenus sont inégaux dans la commune. | 22 |
| `revenus.part_imposee` | Part des ménages fiscaux soumis à l’impôt sur le revenu, en points de pourcentage ; les autres ne paient pas cet impôt. | 21 |

### zonage_abc

| Champ | Explication | Mots |
|---|---|---|
| `zonage_abc.zone` | Classement officiel de la tension du marché du logement, de A bis (le plus tendu) à C (le plus détendu) ; il conditionne certaines aides. | 24 |
| `zonage_abc.reclassement` | Indique si la commune a demandé à changer de zone ; une demande déposée n’est pas une acceptation, la zone affichée reste en vigueur. | 23 |

### vacance

| Champ | Explication | Mots |
|---|---|---|
| `vacance.taux_vacance` | Part des logements privés vides selon le fichier des logements vacants (LOVAC), en pourcentage ; les logements sociaux ne sont pas comptés. | 21 |
| `vacance.nb_vacants_plus_2ans` | Nombre de logements privés vides depuis plus de deux ans selon le fichier des logements vacants (LOVAC) ; les logements sociaux ne sont pas comptés. | 24 |

### fiscalite

| Champ | Explication | Mots |
|---|---|---|
| `fiscalite.taux_tfpb` | Taux voté pour la taxe foncière sur les propriétés bâties (TFPB), en points de pourcentage ; c’est un taux, pas un montant dû. | 22 |
| `fiscalite.taux_tfpnb` | Taux voté pour la taxe foncière sur les propriétés non bâties (TFPNB), en points de pourcentage ; elle vise terrains nus, agricoles ou forestiers. | 23 |
| `fiscalite.taux_th_rs` | Taux voté pour la taxe d’habitation sur les résidences secondaires, en points de pourcentage ; celle sur la résidence principale a été supprimée. | 22 |
| `fiscalite.taux_teom` | Taux voté pour la taxe d’enlèvement des ordures ménagères (TEOM), en points de pourcentage ; elle finance la collecte et le traitement des déchets. | 23 |
| `fiscalite.nom_epci` | Nom de l’intercommunalité (EPCI, établissement public de coopération intercommunale) de la commune ; certaines décisions se prennent à cet échelon. | 19 |

### loyers

| Champ | Explication | Mots |
|---|---|---|
| `loyers.loyer_m2_appartement` | Loyer d’annonce estimé pour un appartement non meublé, charges comprises, en euros par mètre carré et par mois ; ce n’est pas un plafond. | 23 |
| `loyers.loyer_m2_maison` | Loyer d’annonce estimé pour une maison non meublée, charges comprises, en euros par mètre carré et par mois ; ce n’est pas un plafond. | 23 |

### dvf

| Champ | Explication | Mots |
|---|---|---|
| `dvf.prix_m2_median` | Valeur du milieu des prix au mètre carré des ventes signées chez le notaire, pas des annonces ; la moitié est en dessous, l’autre au-dessus. | 24 |
| `dvf.nb_mutations_12m` | Nombre de ventes enregistrées dans la commune sur les douze derniers mois ; un petit nombre rend le prix du milieu moins représentatif. | 22 |
| `dvf.prix_m2_median_maison` | Valeur du milieu des prix au mètre carré des ventes de maisons signées chez le notaire ; les achats sur plan sont exclus. | 22 |
| `dvf.nb_mutations_12m_maison` | Nombre de ventes de maisons retenues pour ce prix ; s’il est trop bas, le prix n’est pas affiché, car il identifierait une vente. | 23 |
| `dvf.prix_m2_median_appartement` | Valeur du milieu des prix au mètre carré des ventes d’appartements signées chez le notaire ; les achats sur plan sont exclus. | 21 |
| `dvf.nb_mutations_12m_appartement` | Nombre de ventes d’appartements retenues pour ce prix ; s’il est trop bas, le prix n’est pas affiché, car il identifierait une vente. | 22 |
| `dvf.tendance.nb_ventes` | Nombre de ventes comptées chaque année ; plus il est petit, moins le prix du milieu est solide. | 17 |
| `dvf.tendance.prix_m2_median` | Valeur du milieu des prix au mètre carré des ventes d’une année ; elle sert à comparer les années entre elles. | 20 |
| `dvf.tendance.evolution_pct` | Écart entre les prix du milieu de la première et de la dernière année ; il suit les ventes, pas la valeur d’un logement. | 23 |
| `dvf.indicateurs.maison.mediane_courante` | Valeur du milieu des prix au mètre carré des maisons vendues récemment ; la période et le nombre de ventes sont indiqués à côté. | 23 |
| `dvf.indicateurs.maison.mediane_precedente` | Même calcul pour les maisons sur la période précédente, pour comparer deux périodes ; la période et le nombre de ventes sont indiqués à côté. | 24 |
| `dvf.indicateurs.appartement.mediane_courante` | Valeur du milieu des prix au mètre carré des appartements vendus récemment ; la période et le nombre de ventes sont indiqués à côté. | 23 |
| `dvf.indicateurs.appartement.mediane_precedente` | Même calcul pour les appartements sur la période précédente, pour comparer deux périodes ; la période et le nombre de ventes sont indiqués à côté. | 24 |
| `dvf.indicateurs.maison.variation_pct` | Évolution en pourcentage entre les deux périodes comparées pour les maisons ; zéro veut dire que la valeur du milieu n’a pas bougé. | 22 |
| `dvf.indicateurs.appartement.variation_pct` | Évolution en pourcentage entre les deux périodes comparées pour les appartements ; zéro veut dire que la valeur du milieu n’a pas bougé. | 22 |

### dpe

| Champ | Explication | Mots |
|---|---|---|
| `dpe.indicateurs.part_fg_pct` | Part des diagnostics de performance énergétique (DPE) classés F ou G, parmi ceux à classe connue ; elle ne décrit pas tout le parc. | 23 |
| `dpe.dpe_passoires_pct` | Part des diagnostics de performance énergétique (DPE) classés F ou G, les plus énergivores ; un DPE est un document, pas tout le parc. | 23 |
| `dpe.dpe_median` | Classe énergie du milieu des diagnostics de performance énergétique (DPE) dont la classe est connue, de A très performant à G très énergivore. | 23 |
| `dpe.annee_construction_mediane` | Année du milieu de construction des logements ayant un diagnostic de performance énergétique (DPE) ; ce n’est pas tout le bâti de la commune. | 23 |

### equipements

| Champ | Explication | Mots |
|---|---|---|
| `equipements.nb_equipements` | Somme des effectifs d’équipements publiés pour la commune ; c’est un compte observé, qui ne prouve pas que la liste est complète. | 21 |

### scolaire

| Champ | Explication | Mots |
|---|---|---|
| `scolaire.nb_etablissements_scolaires` | Nombre d’établissements scolaires distincts référencés pour les secteurs de la commune ; ce n’est pas un recensement complet des écoles. | 19 |

### monuments

| Champ | Explication | Mots |
|---|---|---|
| `monuments.nb_monuments` | Nombre de notices distinctes du patrimoine recensé dans la commune ; une notice ne compte pas forcément un seul objet physique. | 20 |

### foncier_pm

| Champ | Explication | Mots |
|---|---|---|
| `foncier_pm.nb_proprietaires_pm` | Nombre de propriétaires distincts qui sont des personnes morales, comme des sociétés ou des associations ; le chiffre peut être incomplet. | 20 |
| `foncier_pm.surface_pm_ha` | Surface totale des parcelles distinctes détenues par ces personnes morales, en hectares ; une parcelle n’est comptée qu’une fois, le total peut être incomplet. | 23 |

### connectivite

| Champ | Explication | Mots |
|---|---|---|
| `connectivite.fibre_pct` | Part des immeubles avec une technologie fibre optique, en points de pourcentage ; ce taux compte des immeubles, pas des adresses ni des logements. | 23 |

### dpe

| Champ | Explication | Mots |
|---|---|---|
| `bien.dpe.etiquette_dpe` | Classe énergie du logement, de A (très performant) à G (très énergivore), issue de son DPE (diagnostic de performance énergétique). | 20 |
| `bien.dpe.etiquette_ges` | Classe d’émissions de gaz à effet de serre (GES) du logement, de A à G ; elle est distincte de la classe énergie. | 22 |
| `bien.dpe.surface_habitable` | Surface habitable du logement retenue par le diagnostic, en mètres carrés ; elle peut différer de la surface indiquée dans un acte de vente. | 23 |
| `bien.dpe.annee_construction` | Année de construction du logement, telle que déclarée dans son diagnostic ; elle manque parfois, et seule une tranche de période est alors connue. | 23 |
| `bien.dpe.periode_construction` | Tranche de période de construction du logement, utilisée quand l’année exacte manque ; elle suit les grandes étapes des règles de construction thermique. | 22 |
| `bien.dpe.type_batiment` | Nature du bâtiment diagnostiqué, maison individuelle, appartement ou immeuble collectif ; elle aide à savoir quel type de logement est décrit. | 20 |
| `bien.dpe.cout_total_5_usages` | Dépense d’énergie annuelle estimée du logement, en euros par an, pour cinq usages comme le chauffage ; c’est une simulation, pas une facture constatée. | 23 |
| `bien.dpe.energie_chauffage` | Énergie principale qui chauffe le logement, électricité, gaz, fioul, bois ou réseau de chaleur, telle que relevée par le diagnostic. | 20 |
| `bien.dpe.isolation` | Avis du diagnostic sur l’isolation des murs, de la toiture, du plancher bas et des fenêtres ; c’est une appréciation, pas une mesure. | 22 |
| `bien.dpe.conso_5_usages_ep` | Consommation annuelle d’énergie primaire estimée du logement, en kilowattheures par an ; elle inclut les pertes de production et n’est pas une facture. | 22 |

### dvf

| Champ | Explication | Mots |
|---|---|---|
| `bien.dvf.valeur_fonciere` | Prix total de la dernière vente connue, en euros ; il peut couvrir plusieurs lots, comme un appartement et son garage. | 20 |
| `bien.dvf.prix_m2` | Prix au mètre carré de la dernière vente connue, rapporté à la surface bâtie déclarée ; c’est un prix passé, pas une estimation actuelle. | 23 |
| `bien.dvf.date_mutation` | Date de la dernière vente connue ; les ventes arrivent avec quelques mois de retard, une vente très récente peut manquer. | 20 |
| `bien.dvf.type_local` | Nature du local vendu lors de la dernière vente, maison, appartement, dépendance ou local d’activité ; cela dit ce qui a été acheté. | 22 |
| `bien.dvf.surface_reelle_bati` | Surface bâtie déclarée dans l’acte de la dernière vente, en mètres carrés ; elle peut différer de la surface relevée par un diagnostic. | 22 |
| `bien.dvf.nombre_pieces_principales` | Nombre de pièces principales déclarées dans l’acte, chambres et séjour ; la cuisine, la salle de bains et les couloirs ne sont pas comptés. | 23 |
| `bien.dvf.classe_mutation` | Ce que couvre la vente, logement seul, plusieurs logements, mélange logement et local commercial, terrain ; un prix global ne se divise pas. | 22 |
| `bien.dvf.surface_bati_totale` | Surface bâtie totale de la vente, additionnée sur tous ses locaux ; si elle en porte plusieurs, ce n’est pas la surface d’un seul logement. | 24 |

### cadastre

| Champ | Explication | Mots |
|---|---|---|
| `bien.cadastre.contenance` | Surface de la parcelle cadastrale, en mètres carrés ; c’est le terrain où se trouve le bien, pas la surface habitable de la construction. | 23 |

### risques

| Champ | Explication | Mots |
|---|---|---|
| `bien.risques.inondation` | Indique si l’inondation figure parmi les risques recensés pour la commune ; cela ne prouve pas que le bien est inondable. | 20 |
| `bien.risques.sismique` | Indique si le séisme figure parmi les risques de la commune ; cela ne dit ni le niveau de sismicité ni la solidité du bâtiment. | 24 |
| `bien.risques.expo_argiles` | Exposition du point consulté au retrait-gonflement des argiles, des sols qui gonflent et se rétractent avec l’humidité ; ce n’est pas une étude de sol. | 24 |

### urbanisme

| Champ | Explication | Mots |
|---|---|---|
| `bien.urbanisme.zonage_libelle` | Nom de la zone du plan local d’urbanisme (PLU) au point consulté ; ce zonage encadre les constructions, mais une parcelle peut recouper plusieurs zones. | 24 |
| `bien.urbanisme.type_zone` | Grande catégorie de la zone d’urbanisme au point consulté, urbaine, à urbaniser, agricole ou naturelle ; elle ne décrit pas toute la parcelle. | 22 |

### loyers

| Champ | Explication | Mots |
|---|---|---|
| `loyers.prediction.loyer_m2` | Loyer d’annonce estimé pour ce type d’habitat non meublé, en euros par mètre carré et par mois, charges comprises ; ce n’est pas un plafond. | 24 |
| `loyers.prediction.loyer_m2_bas` | Borne basse du loyer estimé, en euros par mètre carré et par mois ; elle décrit l’incertitude du modèle, pas la dispersion des loyers. | 23 |
| `loyers.prediction.loyer_m2_haut` | Borne haute du loyer estimé, en euros par mètre carré et par mois ; elle décrit l’incertitude du modèle, pas la dispersion des loyers. | 23 |
| `loyers.prediction.r2_ajuste` | Score d’ajustement du modèle (R² ajusté), sans unité ; plus il est proche de 1, mieux le modèle reproduit les loyers observés. | 21 |
| `loyers.prediction.r2` | Ancien nom du score d’ajustement du modèle, gardé pour compatibilité ; même valeur, sans unité, qui ne se lit pas comme une probabilité. | 22 |
| `loyers.prediction.nb_observations_commune` | Nombre d’annonces de location observées dans la commune ; zéro veut dire qu’aucune n’a été vue, et l’estimation vient d’un territoire plus large. | 22 |
| `loyers.prediction.nb_observations_maille` | Nombre d’annonces observées dans le territoire d’estimation, qui regroupe plusieurs communes ; il ne s’ajoute pas à celui de la commune. | 20 |
| `loyers.prediction.nb_observations` | Ancien nom du nombre d’annonces observées dans la commune, gardé pour compatibilité ; même valeur que le champ actuel. | 18 |
| `loyers.prediction.trimestre_reference` | Trimestre de l’année auquel se rapportent les annonces, de 1 à 4 ; il situe l’estimation dans l’année, sans montrer une évolution. | 21 |
| `loyers.prediction.surface_reference_m2` | Surface habitable du logement de référence, en mètres carrés ; un logement plus grand ou plus petit peut avoir un autre prix au mètre carré. | 24 |
| `loyers.prediction.surface_piece_reference_m2` | Surface moyenne par pièce du logement type de référence, en mètres carrés ; elle décrit la taille de référence utilisée pour l’estimation. | 21 |

### dvf

| Champ | Explication | Mots |
|---|---|---|
| `dvf.indicateurs.effectif` | Nombre de ventes de maisons ou d’appartements retenues pour ce calcul ; un petit nombre rend la valeur du milieu peu représentative. | 21 |
| `dvf.indicateurs.quantiles.p10` | Prix au mètre carré sous lequel se situe un dixième des ventes de la catégorie ; publié seulement quand le calcul est validé. | 22 |
| `dvf.indicateurs.quantiles.p50` | Valeur du milieu des prix au mètre carré de la catégorie sur la période récente ; publié seulement si le calcul est validé. | 22 |
| `dvf.indicateurs.quantiles.p90` | Prix au mètre carré sous lequel se situent neuf dixièmes des ventes de la catégorie ; publié seulement quand le calcul est validé. | 22 |

### dpe

| Champ | Explication | Mots |
|---|---|---|
| `dpe.indicateurs.population.total` | Nombre de diagnostics de performance énergétique (DPE) de la commune, sans ceux que la source a supprimés ; ce n’est pas le nombre de logements. | 24 |
| `dpe.indicateurs.population.classes_connues` | Nombre de diagnostics de performance énergétique (DPE) de la commune dont la classe énergie est connue ; c’est la base des parts calculées par classe. | 24 |
| `dpe.indicateurs.population.types_batiment` | Répartition des diagnostics de performance énergétique (DPE) selon le bâtiment déclaré, maison, appartement, autre ou inconnu ; ces nombres font le total. | 21 |
| `dpe.indicateurs.population.validite` | Nombre de diagnostics de performance énergétique (DPE) selon leur validité à la date d’évaluation, comme encore valide ou expiré ; ces nombres font le total. | 24 |
| `dpe.indicateurs.population.remplacement` | Nombre de diagnostics de performance énergétique (DPE) remplacés par un plus récent, ou non ; les cas ambigus ou non vérifiés sont comptés à part. | 24 |
| `dpe.indicateurs.population.regimes` | Nombre de DPE (diagnostic de performance énergétique) par jeu de données ; un même DPE peut compter dans deux jeux, ces nombres ne s’additionnent pas. | 24 |
| `dpe.indicateurs.population.source_inconnue` | Nombre de DPE (diagnostic de performance énergétique) dont l’état dans la source publique n’est pas connu ; ce compte limite la lecture des autres chiffres. | 24 |
| `dpe.indicateurs.population.revision_non_verifiee` | Nombre de DPE (diagnostic de performance énergétique) dont la version la plus récente n’a pas été vérifiée ; aujourd’hui ce nombre égale le total. | 23 |
| `dpe.indicateurs.distribution.classes` | Nombre de DPE (diagnostic de performance énergétique) de la commune dans chaque classe énergie, de A très performant à G très énergivore. | 22 |
| `dpe.indicateurs.distribution.inconnue` | Nombre de DPE (diagnostic de performance énergétique) de la commune sans classe énergie connue ; ces documents n’entrent dans aucune classe de A à G. | 24 |
| `dpe.indicateurs.dates_etablissement_inconnues` | Nombre de DPE (diagnostic de performance énergétique) dont la date d’établissement n’est pas connue ; cette date indique quelle méthode de calcul a servi. | 23 |
| `dpe.indicateurs.modes` | Classe énergie la plus fréquente parmi les DPE (diagnostic de performance énergétique) à classe connue ; en cas d’égalité, toutes sont données. | 21 |
| `dpe.indicateurs.mediane_ordinale` | Classe énergie du milieu parmi les DPE (diagnostic de performance énergétique) à classe connue, de A à G ; absente si aucune n’est connue. | 23 |

### adresse

| Champ | Explication | Mots |
|---|---|---|
| `adresse.dpe.nombre_declares` | Nombre de DPE (diagnostic de performance énergétique) déclarés avec cette adresse saisie ; aucun n’est rattaché à un logement précis. | 19 |
| `adresse.dpe.plus_recent` | DPE (diagnostic de performance énergétique) le plus récent déclaré à cette adresse ; il n’est rattaché à aucun logement précis. | 19 |
| `adresse.dpe.constat_d110` | Constat donné quand tout concorde, une maison, un seul bâtiment et un seul DPE (diagnostic de performance énergétique) ; il ne prouve pas l’identité d’un logement. | 25 |
| `adresse.dpe.documents_adresse_brute` | Nombre de DPE (diagnostic de performance énergétique) dont l’adresse saisie correspond exactement à cette adresse ; ce n’est pas une identité de logement. | 22 |

### dpe

| Champ | Explication | Mots |
|---|---|---|
| `dpe.document.etiquette_dpe` | Classe énergie, de A très performant à G très énergivore, déclarée par ce DPE (diagnostic de performance énergétique) ; elle décrit ce qui est déclaré. | 24 |
| `dpe.document.etiquette_ges` | Classe climat, de A peu émettrice à G très émettrice, déclarée par ce DPE (diagnostic de performance énergétique) ; elle se lit à part. | 23 |
| `dpe.document.surface_habitable` | Surface habitable déclarée par ce DPE (diagnostic de performance énergétique), en mètres carrés, telle que saisie par le diagnostiqueur. | 19 |
| `dpe.document.adresse_declaree` | Adresse saisie par le diagnostiqueur, si elle diffère de l’adresse retenue ; elle n’est pas affichée quand elle porte un complément. | 20 |
| `dpe.document.conso_ep` | Consommation annuelle d’énergie primaire déclarée par ce DPE (diagnostic de performance énergétique), en kilowattheures par an ; c’est une estimation, pas une facture. | 22 |
| `dpe.document.conso_ep_m2` | Consommation d’énergie primaire par mètre carré et par an, publiée pour ce DPE (diagnostic de performance énergétique) ; elle n’est jamais recalculée. | 21 |
| `dpe.document.emissions_ges_m2` | Émissions de gaz à effet de serre par mètre carré et par an, publiées pour ce DPE (diagnostic de performance énergétique) ; jamais recalculées. | 23 |
| `dpe.document.emissions_ges` | Gaz à effet de serre émis par an selon ce DPE (diagnostic de performance énergétique), en kilogrammes d’équivalent dioxyde de carbone ; c’est une estimation standardisée. | 25 |
| `dpe.document.cout_annuel` | Dépense d’énergie annuelle estimée déclarée par ce DPE (diagnostic de performance énergétique), en euros par an ; c’est une simulation, pas une facture constatée. | 23 |
| `dpe.document.type_batiment` | Nature du bâtiment décrit par ce DPE (diagnostic de performance énergétique), maison, appartement ou immeuble collectif, telle que déclarée par le diagnostiqueur. | 22 |
| `dpe.document.periode_construction` | Tranche de période de construction déclarée par ce DPE (diagnostic de performance énergétique) ; elle suit les grandes étapes des règles de construction thermique. | 23 |
| `dpe.document.energie_chauffage` | Énergie principale de chauffage déclarée par ce DPE (diagnostic de performance énergétique), comme le gaz, le fioul ou le bois. | 20 |
| `dpe.document.installation_chauffage` | Type d’installation de chauffage déclaré par ce DPE (diagnostic de performance énergétique), individuelle, collective ou mixte. | 16 |
| `dpe.document.isolation` | Avis porté par ce DPE (diagnostic de performance énergétique) sur l’isolation de l’enveloppe, murs, toiture, plancher bas et ouvertures ; c’est une appréciation. | 22 |
| `dpe.document.date_etablissement` | Date à laquelle le diagnostiqueur a établi ce DPE (diagnostic de performance énergétique) ; elle indique la méthode de calcul utilisée. | 20 |
| `dpe.document.date_fin_validite` | Date de fin de validité déclarée par ce DPE (diagnostic de performance énergétique) ; elle n’est jamais calculée, et un document dépassé reste listé. | 23 |
| `dpe.document.methode` | Méthode de calcul de ce DPE (diagnostic de performance énergétique), ancienne ou actuelle selon sa date ; deux méthodes différentes ne se comparent pas. | 23 |
| `dpe.document.adresse_ban` | Adresse de la Base Adresse Nationale (BAN) déclarée pour ce DPE (diagnostic de performance énergétique), reprise sans vérification ; elle ne désigne aucun logement. | 23 |

### adresse

| Champ | Explication | Mots |
|---|---|---|
| `adresse.dvf.ventes_parcelle.nombre` | Nombre de ventes sur la parcelle cadastrale du point de l’adresse, sur toute la période importée ; une vente de plusieurs parcelles compte une fois. | 24 |
| `adresse.dvf.ventes_parcelle.derniere` | Date de la vente la plus récente enregistrée sur la parcelle cadastrale où se trouve le point de l’adresse. | 19 |
| `adresse.risques.argiles_au_point` | Exposition du point de l’adresse au retrait-gonflement des argiles, le jour de la consultation ; elle ne dit rien des autres risques. | 21 |
| `adresse.reference_commune.prix_median_maison` | Valeur du milieu des prix au mètre carré des maisons vendues dans la commune, en référence ; ce n’est pas une estimation de l’adresse. | 23 |
| `adresse.reference_commune.prix_median_appartement` | Valeur du milieu des prix au mètre carré des appartements vendus dans la commune, en référence ; ce n’est pas une estimation de l’adresse. | 23 |
| `adresse.dvf.vente.prix` | Prix total de la vente, qui peut porter sur plusieurs locaux ; il n’est rattaché à aucun logement précis. | 18 |
| `adresse.dvf.vente.prix_m2` | Prix total divisé par la surface bâtie, calculé seulement pour la vente d’un seul logement ; ce n’est pas une estimation de l’adresse. | 22 |
| `adresse.dvf.vente.surface_batie` | Surface bâtie réelle déclarée pour une vente, somme de tous ses locaux ; elle ne décrit aucun logement précis. | 18 |
| `adresse.dvf.ventes_ressemblantes.effectif` | Nombre de ventes d’une seule maison ou d’un seul appartement comparables, selon les critères affichés ; aucune n’est rattachée à un logement. | 21 |
| `adresse.dvf.ventes_adresse.nombre` | Nombre de ventes rattachées à l’adresse saisie dans l’acte ; zéro ne veut pas dire aucune vente, car une partie ne retrouve pas son adresse. | 24 |
| `adresse.batiments.nombre_parcelle` | Nombre de bâtiments recensés sur la parcelle du point de l’adresse, annexes comprises ; il ne désigne aucun bâtiment précis de l’adresse. | 21 |
| `adresse.batiments.nombre_parcelle_hors_annexe` | Nombre de bâtiments de la parcelle, sans les annexes ; un bâtiment dont l’usage est inconnu reste compté. | 17 |
| `adresse.batiment.hauteur` | Hauteur en mètres du bâtiment qui contient le point de l’adresse ; donnée seulement quand un seul bâtiment contient ce point. | 20 |
| `adresse.batiment.etages` | Nombre d’étages du bâtiment qui contient le point de l’adresse ; donné seulement quand un seul bâtiment contient ce point. | 19 |
| `adresse.parcelle.contenance` | Surface cadastrale, en mètres carrés, de la parcelle où se trouve le point de l’adresse ; c’est le terrain, pas la surface habitable. | 22 |
| `adresse.copropriete.immatriculation` | Nombre de copropriétés immatriculées sur la parcelle de l’adresse ; aucun lot ni logement n’en est déduit. | 16 |
| `adresse.risques.liste_commune` | Risques naturels et technologiques recensés pour la commune de l’adresse le jour de la consultation ; c’est une référence communale, pas une exposition de l’adresse. | 24 |
| `adresse.risques.radon_commune` | Catégorie du potentiel radon de la commune, de 1 faible à 3 significatif ; c’est une référence communale, pas une mesure à l’adresse. | 22 |
| `adresse.risques.icpe_commune` | Nombre d’installations classées pour la protection de l’environnement (ICPE) dans la commune ; c’est une référence communale, pas une distance. | 19 |
| `adresse.urbanisme.zone_plu` | Zone du document d’urbanisme, plan local d’urbanisme ou carte communale, qui contient le point de l’adresse, le jour de la consultation. | 21 |
| `adresse.urbanisme.prescriptions` | Règles du document d’urbanisme qui s’appliquent au point de l’adresse, par exemple un espace boisé classé, le jour de la consultation. | 21 |
| `adresse.connectivite.eligibilite_fibre` | Éligibilité à la fibre de l’immeuble, si un seul immeuble porte ce numéro et cette voie ; elle ne dit rien d’un abonnement. | 22 |
| `adresse.connectivite.debit` | Classe de débit descendant publiée pour la fibre de cet immeuble ; elle décrit le réseau, pas un abonnement précis. | 19 |
| `adresse.transports.arret_proche` | Arrêts de transport et gares proches, à moins d’un kilomètre, avec leur distance à vol d’oiseau ; ce n’est pas un temps de trajet. | 23 |

### departement

| Champ | Explication | Mots |
|---|---|---|
| `departement.population.total` | Nombre d’habitants du département, somme des populations des communes chargées ; si peu de communes sont comptées, le total est partiel. | 20 |
| `departement.population.communes_comptees` | Nombre de communes dont la population entre dans le total du département, pour savoir sur combien de communes il repose. | 20 |
| `departement.prix.appartement.mediane_m2` | Valeur du milieu des prix au mètre carré des appartements du département sur la période récente ; pas une moyenne des communes. | 21 |
| `departement.prix.appartement.effectif` | Nombre de ventes d’appartements retenues pour ce prix sur la période récente ; peu de ventes rend le prix moins représentatif. | 20 |
| `departement.prix.maison.mediane_m2` | Valeur du milieu des prix au mètre carré des maisons du département sur la période récente ; pas une moyenne des communes. | 21 |
| `departement.prix.maison.effectif` | Nombre de ventes de maisons retenues pour ce prix sur la période récente ; peu de ventes rend le prix moins représentatif. | 21 |
| `departement.dpe.part_fg_pct` | Part des DPE (diagnostic de performance énergétique) classés F ou G ; ce sont des documents, pas une part des logements du département. | 22 |
| `departement.dpe.classes_connues` | Nombre de DPE (diagnostic de performance énergétique) du département à classe connue ; c’est la base de la part des classes F et G. | 23 |

## 3. « Liste incomplète : non » et autres drapeaux techniques

Décision de l’orchestrateur appliquée : un drapeau technique ne s’affiche que lorsqu’il porte une réserve, suivi de sa raison. « Liste complète : oui » n’est jamais écrit (on ne sait pas si la source recense tout).

| Drapeau servi | Affiché quand | Libellé | Raison affichée |
|---|---|---|---|
| `lecture_complete` | faux | Lecture incomplète | La source n’a renvoyé qu’une partie des prélèvements ; les chiffres ne portent que sur ceux qui ont été lus. |
| `reseaux_complets` | faux | Liste des réseaux incomplète | La liste des réseaux d’eau de la commune n’a pas pu être lue en entier. |
| `plus_proche_connue` | vrai | Gare la plus proche connue | Une gare plus proche peut exister dans un département que DataCommune ne couvre pas encore. |
| `liste_incomplete` (seveso) | vrai | Liste incomplète | La source n’a renvoyé qu’une partie de la liste ; d’autres éléments peuvent exister. |
| `liste_incomplete` (equipements) | vrai | Liste incomplète | Le rayon déborde sur un département que DataCommune ne couvre pas encore ; d’autres éléments peuvent s’y trouver. |
| `liste_incomplete` (transports) | vrai | Liste incomplète | Le rayon déborde sur un département non couvert, une catégorie d’arrêts n’est pas importée ou la liste est écourtée ; d’autres arrêts peuvent exister. |
| `liste_incomplete` (construction) | vrai | Liste incomplète | Seule une partie des groupes de bâtiments de la parcelle est listée ici. |
| `liste_incomplete` (copropriete) | vrai | Liste incomplète | Seule une partie des copropriétés de la parcelle est listée ici. |
| `liste_incomplete` (autre thème) | vrai | Liste incomplète | La source n’a pas permis de tout lister ; d’autres éléments peuvent exister. |
| `liste_incomplete` (ventes ressemblantes, volet) | vrai | Liste incomplète | Le rayon déborde sur un département que DataCommune ne couvre pas encore ; des ventes ressemblantes peuvent s’y trouver. |

Masqués quand ils valent faux, gardés en « oui » sinon : `un_seul_batiment_etabli`, `lue_sur_la_commune`, `diffuse` (délinquance, colonne « Chiffre publié pour la commune »). Les booléens qui informent gardent leur « non » : `dernier_conforme` (eau), `electrifiee` (voie ferrée), `majoree` (taxe), `charges_comprises` (loyers).

Thèmes de « lecture coupée » : catnat, seveso, grands axes, voies ferrées, cavités, urbanisme, installations classées, risques recensés. Thèmes de « rayon hors couverture » : antennes 5G, équipements.
