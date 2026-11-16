# LOVAC — témoin officiel COR-08

Source publique : [LOVAC 2020–2026, CEREMA / ministère](https://www.data.gouv.fr/datasets/logements-vacants-du-parc-prive-par-commune-departement-region-france-de-2020-a-2026). Ressource CSV et empreintes exactes : `manifest.json`. Téléchargement borné à 30 Mo ; reçu 7 628 201 octets le 8 septembre 2026.

Le CSV est un sous-ensemble de cinq lignes officielles, avec toutes les colonnes et les valeurs inchangées. La sérialisation CSV est décrite dans le manifeste ; les fins de ligne sont figées par `.gitattributes`. L'oracle de taux dans le test relit les colonnes sources et calcule indépendamment en Decimal, sans appeler le calcul ETL.

| Code public | Témoin |
|---|---|
| 01001 | Dénominateur connu, vacance totale connue, longue durée secrète |
| 01006 | Effectifs et dénominateur secrets |
| 01009 | Numérateur positif 28, dénominateur secret : aucun taux calculable |
| 02343 | Vrai zéro, dénominateur positif 35 |
| 44109 | Commune Nantes, valeurs connues |

La source utilise `s` pour les valeurs sous secret statistique. Le dénominateur est `ff_pp_total_25`, le parc privé issu des Fichiers fonciers 2025, et le numérateur `pp_vacant_26`, la vacance LOVAC 2026. Cette paire existante est conservée, sans substitution d'un parc INSEE ou prétention à deux millésimes identiques.

Partition nationale du dénominateur dans le fichier figé : 16 591 numériques, 18 321 secrets `s`, **aucun vide**. Les lignes absentes, négatives et incohérentes des tests sont des mutations synthétiques explicitement séparées du témoin officiel. Ce jeu ne prouve aucune réparation d'une base partagée.
