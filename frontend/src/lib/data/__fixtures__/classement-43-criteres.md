## Les 43 critères

Les trois colonnes d'adresses donnent l'état servi. Zéro, manque, hors couverture et panne restent distincts.

| Rang | Critère | `id` | Catégorie | P | D | C | V | Note | Preuve | Niveau | Remonte | Nantes | La Roche | Mamoudzou | Raison |
|---:|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|---|
| 1 | Prix au m² | prix_commune | Les prix | 3 | 3 | 3 | 2 | 28 | M | visible à l'arrivée | non | valeur | valeur | hors couverture | Premier critère mesuré (Ipsos 76 %, Orpi 64 %). Valeur de commune, absente à Mayotte. |
| 2 | Loyers | loyers | Les prix | 2 | 3 | 3 | 2 | 25 | J | visible à l'arrivée | non | valeur | valeur | hors couverture | Demandé par Ianis. Sert aux locataires et aux investisseurs ; pas d'enquête récente isolée. |
| 3 | DPE | dpe | Le logement et sa parcelle | 3 | 3 | 2 | 2 | 26 | M | visible à l'arrivée | si vigilance | valeur | zéro | zéro | Critère déclaré et effet sur les prix. Valeur sur 1 adresse sur 3 : place tenue par décision d'Ianis, avec un état zéro utile. |
| 4 | Inondation | inondation | Ce qui peut arriver ici | 2 | 3 | 3 | 3 | 27 | M | un clic | si vigilance | valeur | valeur | valeur | Sans verrou, deuxième. Peut changer l'achat ; nommé à l'arrivée s'il touche l'adresse. Vigilance sur 3 adresses sur 3 : le signal discrimine peu. |
| 5 | Ventes de la parcelle | ventes_parcelle | Les prix | 3 | 3 | 3 | 1 | 26 | J | un clic | si vigilance | valeur | manque | hors couverture | Le vrai prix payé ici, quand il existe. Rattaché sur 1 adresse sur 3. |
| 6 | Équipements | equipements | La vie au quotidien | 3 | 2 | 2 | 2,5 | 24 | M | un clic | si vigilance | valeur | valeur | zéro | Commerces 52 %, santé 48 % (Orpi). Un compte à rendre lisible. |
| 7 | Écoles publiques | ecoles | La vie au quotidien | 2 | 2 | 3 | 3 | 24 | O | un clic | non | valeur | valeur | valeur | Effet sur les prix à Paris (Fack et Grenet) ; pas d'enquête récente. |
| 8 | Grands axes routiers | grand_axe | La vie au quotidien | 2 | 2 | 3 | 2,5 | 23 | D | un clic | si vigilance | zéro | valeur | valeur | Bruit de la rue : surprise fréquente (Qualitel). Une distance se comprend seule. |
| 9 | Transports | transports | La vie au quotidien | 2 | 2 | 3 | 2 | 22 | M | un clic | si vigilance | valeur | valeur | hors couverture | Orpi 31 %, Optimhome 23 %. Hors couverture à Mayotte. |
| 10 | Délinquance enregistrée | delinquance | La vie au quotidien | 3 | 1 | 2 | 3 | 22 | D | un clic | non | valeur | valeur | valeur | Calme et sécurité comptent ; valeur de commune, seul chiffre déjà comparé au département. |
| 11 | Argiles | argiles | Ce qui peut arriver ici | 2 | 2 | 2 | 2 | 20 | J | un clic | si vigilance | valeur | valeur | manque | Fissures : coût réel. Remonte si le point d'adresse est en vigilance. |
| 12 | Taxe foncière | fiscalite | Les prix | 2 | 2 | 1 | 3 | 20 | D | un clic | non | valeur | valeur | valeur | Coût annuel du propriétaire ; charges 11 % des surprises (Qualitel). Un taux se comprend mal sans montant. |
| 13 | Secteur affecté par le bruit | secteur_bruit | La vie au quotidien | 3 | 2 | 1 | 1 | 19 | D | un clic | si vigilance | valeur | panne | panne | Calme 41 % (Optimhome). En panne sur 2 adresses sur 3 ; à nommer en mots simples. |
| 14 | Fibre | fibre | Le logement et sa parcelle | 2 | 1 | 3 | 2 | 19 | M | un clic | si vigilance | valeur | valeur | manque | Numérique 27 % (Orpi). Réponse simple, rarement décisive. |
| 15 | Catastrophes naturelles | catnat | Ce qui peut arriver ici | 2 | 1 | 2 | 3 | 19 | J | un clic | non | valeur | valeur | valeur | Historique communal parlant, sans dire si l'adresse a été touchée. |
| 16 | Construction | construction | Le logement et sa parcelle | 2 | 2 | 3 | 0 | 18 | J | un clic | si vigilance | manque | manque | hors couverture | Âge du bâti : utile. Absent sur 3 adresses sur 3 : quitte la tête (R9). |
| 17 | Copropriété | copropriete | Le logement et sa parcelle | 2 | 2 | 2 | 1 | 18 | J | un clic | si vigilance | zéro | manque | zéro | Charges et travaux pour les appartements ; aucun chiffre de preuve. |
| 18 | Voies ferrées | voie_ferree | La vie au quotidien | 1 | 2 | 3 | 1,5 | 18 | J | un clic | si vigilance | zéro | zéro | zéro | Concerne peu d'adresses, mais change la vie de celles-ci. |
| 19 | Cavités et mouvements de terrain | mouvements_terrain | Ce qui peut arriver ici | 1 | 2 | 2 | 2 | 17 | J | un clic | si vigilance | zéro | zéro | valeur | Remonte s'il touche le rayon de l'adresse. |
| 20 | Sites Seveso | seveso | Ce qui peut arriver ici | 1 | 2 | 2 | 2 | 17 | J | un clic | non | valeur | zéro | zéro | Rare, grave, rayon de 5 km : reste dans les risques. |
| 21 | Bâtiment | batiment | Le logement et sa parcelle | 2 | 1 | 2 | 2 | 17 | J | un clic | si vigilance | valeur | manque | valeur | Hauteur et étages : contexte du logement, pas une décision. |
| 22 | Qualité de l'air | qualite_air | La vie au quotidien | 2 | 1 | 1 | 3 | 17 | J | un clic | non | valeur | valeur | valeur | Indice sur 6 à expliquer ; valeur de commune. |
| 23 | Risques recensés | risques_commune | Ce qui peut arriver ici | 2 | 1 | 1 | 3 | 17 | J | un clic | non | valeur | valeur | valeur | Liste communale : ne dit rien de l'adresse. |
| 24 | Trait de côte | trait_de_cote | Ce qui peut arriver ici | 1 | 3 | 2 | 0 | 16 | J | deux clics | non | sans objet | sans objet | sans objet | Grave mais local. Sans objet sur 3 adresses sur 3 ; à remonter là où il s'applique. |
| 25 | Bruit des avions | peb | La vie au quotidien | 1 | 2 | 2 | 1,5 | 16 | J | deux clics | si vigilance | zéro | zéro | zéro | Concerne les abords d'aéroports. Aucun sur 3 adresses sur 3. |
| 26 | Parcelle | parcelle | Le logement et sa parcelle | 1 | 1 | 3 | 2 | 16 | J | deux clics | si vigilance | valeur | manque | valeur | Surface utile surtout à une maison. |
| 27 | Sismicité | sismicite | Ce qui peut arriver ici | 1 | 1 | 2 | 3 | 16 | J | deux clics | non | valeur | valeur | valeur | Zone communale, peu de lecteurs concernés hors zones fortes. |
| 28 | Altitude | altitude | Ce qui peut arriver ici | 1 | 0 | 3 | 3 | 15 | J | deux clics | si vigilance | valeur | valeur | valeur | Disponible et clair, mais sans effet sur la décision. |
| 29 | Rupture de barrage | rupture_barrage | Ce qui peut arriver ici | 1 | 1 | 2 | 2 | 14 | J | deux clics | non | valeur | valeur | sans objet | Fait communal, sans lien direct avec l'adresse. |
| 30 | Radon | radon | Ce qui peut arriver ici | 1 | 1 | 1 | 3 | 14 | J | deux clics | non | valeur | valeur | valeur | « Trop précis » pour Ianis. Reste accessible, jamais à l'arrivée sans signal à l'adresse. |
| 31 | Eau du robinet | eau_potable | La vie au quotidien | 2 | 0 | 1 | 3 | 14 | J | deux clics | non | valeur | valeur | valeur | Dureté en degrés : rarement décisif, unité à traduire. |
| 32 | Encadrement des loyers | encadrement_loyers | Les prix | 1 | 2 | 2 | 0 | 13 | J | deux clics | non | sans objet | sans objet | sans objet | Sans objet sur 3 adresses sur 3 ; à remonter près du loyer là où il s'applique. |
| 33 | Sols pollués | pollution_sols | Ce qui peut arriver ici | 1 | 2 | 2 | 0 | 13 | J | deux clics | si vigilance | manque | manque | manque | Important si renseigné. Non publié sur 3 adresses sur 3. |
| 34 | Réseau mobile 5G | antennes_5g | La vie au quotidien | 1 | 0 | 2 | 3 | 13 | J | deux clics | si vigilance | valeur | valeur | valeur | Compte de supports : peu parlant. |
| 35 | Nucléaire | nucleaire | Ce qui peut arriver ici | 1 | 1 | 3 | 0 | 12 | J | deux clics | non | sans objet | sans objet | sans objet | Sans objet sur 3 adresses sur 3. |
| 36 | Remontée de nappe | remontee_nappe | Ce qui peut arriver ici | 1 | 1 | 1 | 2 | 12 | J | deux clics | non | valeur | valeur | sans objet | Fait communal, terme à expliquer. |
| 37 | Revenus | revenus | La vie au quotidien | 1 | 1 | 1 | 2 | 12 | J | deux clics | non | valeur | valeur | hors couverture | Contexte du quartier ; revenu déclaré à la commune. |
| 38 | Installations classées | icpe_commune | Ce qui peut arriver ici | 1 | 1 | 0 | 3 | 12 | J | deux clics | non | valeur | valeur | valeur | Compte communal, sigle : pour les curieux. |
| 39 | Zonage ABC | zonage_abc | Les prix | 1 | 1 | 0 | 3 | 12 | J | deux clics | non | valeur | valeur | valeur | Sert à quelques aides ; jargon administratif. |
| 40 | Urbanisme | urbanisme | Le logement et sa parcelle | 1 | 2 | 1 | 0 | 11 | J | deux clics | si vigilance | panne | panne | panne | Utile aux projets de travaux. En panne sur 3 adresses sur 3 : à corriger avant tout rang. |
| 41 | Débroussaillement | debroussaillement | Ce qui peut arriver ici | 1 | 1 | 2 | 0 | 10 | J | deux clics | si vigilance | manque | manque | manque | Non publié sur 3 adresses sur 3. |
| 42 | Zone tendue | zone_tendue | Les prix | 1 | 1 | 1 | 1 | 10 | J | deux clics | non | valeur | sans objet | sans objet | Règle du marché locatif, utile aux bailleurs. |
| 43 | Géologie | geologie | Ce qui peut arriver ici | 0 | 0 | 0 | 1 | 2 | J | deux clics | non | valeur | panne | manque | Pour spécialistes. |

