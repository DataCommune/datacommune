# Tests réels et preuves — 10 septembre 2026

## Environnement et protocole

- Service testé : `https://dev-api.datacommune.fr`, **préproduction**. Version identique avant et après les deux séries : `41f140a686de23ada10bcc5d5e0ce8d5e8510fc4`, schéma `096_commune_indicator_build_activation`.
- Code lu : worktree intégré `C:/tmp/datacommune-phase591-20260910`, commit documentaire `3394395a` ; le reçu existant `docs/reprise-2026-09-10/phase591/final-41f140a6/runtime.json` identifie les arbres applicatifs servis.
- Série principale : 16 adresses, du 10/09/2026 20:40:15 à 20:41:08 UTC ; 16 réponses Resolve HTTP 200 et 32 réponses ADEME HTTP 200, deux datasets par cas. L'heure précise de chaque capture est conservée.
- Sources interrogées directement : `dpe03existant` et `dpe02neuf`, filtre `identifiant_ban`, sans utiliser DataCommune comme référence attendue. Des requêtes préalables par commune/type ont permis de sélectionner des contradictions dans la source.
- Série complémentaire : quatre consultations de document (R04, R06, R12, R14), et toutes les pages R14. Réponses et versions conservées dans `evidence/details`.
- Contrôle navigateur frais R06 : page Bien, ouverture « Consulter le document 01 ». Rio avait également été observée dans l'onglet utilisateur. Aucune publication ni modification de donnée métier.

Les appels sont bornés et espacés ; pas de scan national. Les identifiants d'accès et références signées sont retirés des captures conservées. Les adresses sont utilisées comme cas de données publiques, sans identité des occupants.

## Résultat des 16 recherches

Les comptes ADEME concernent la **clé de référence du cas**. R09 et R11 utilisent délibérément une adresse brute différente de l'adresse BAN source : les comptes de leurs deux colonnes ne sont donc pas des populations identiques.

| Cas | Adresse réellement recherchée | ADEME existant + neuf | Inventaire DataCommune | Résultat |
|---|---|---:|---:|---|
| R01 | 2 rue Rio, Rezé | 0 + 0 | 0 | Absence dans cette recherche, même clé BAN |
| R02 | 16 rue des Clos Mâts, Brains | 0 + 2 | 2 | Documents trouvés, attribution non établie |
| R03 | 5 allée du Tertre, Indre | 0 + 1 | 1 | Idem |
| R04 | 2b rue du Martin Pêcheur, Le Fenouiller | 0 + 1 | 1 | Idem ; maison neuve, suffixe |
| R05 | 39 rue Blanchet, Rezé | 1 + 1 | 2 | Idem ; deux régimes source |
| R06 | 105 rue Aristide-Briand, Rezé | 1 + 0 | 1 | D / 79 m² présent, synthèse « non disponible » |
| R07 | 14 rue des Bouleaux, Rezé | 1 + 0 | 1 | Document trouvé, attribution non établie |
| R08 | 113 rue des Carterons, Rezé | 1 + 0 | 1 | Source brute indiquant le 111 |
| R09 | 111 rue des Carterons, Rezé | 1 + 0 au BAN du 113 | 0 au BAN du 111 | Chemin adresse brute absent |
| R10 | 21 rue Benoît-Chupiet, Rezé | 19 + 0 | 19 | Documents trouvés, attribution non établie |
| R11 | 38 rue Alexandre-Huchon, Rezé | 19 + 0 au BAN du 21 Chupiet | 0 au BAN du 38 Huchon | Un document source mentionne le 38 Huchon |
| R12 | 41 rue Stendhal, Nantes | 1 + 0 | 1 | Document trouvé ; prédécesseur manquant dans le graphe local |
| R13 | 5 avenue des Coucous, Nantes | 2 + 0 | 2 | Documents trouvés, attribution non établie |
| R14 | 20 route de Carquefou, Nantes | 114 + 0 | 114 | Six pages, 114 références distinctes lues |
| R15 | 7 rue Gaston-Tessier, La Roche-sur-Yon | 1 + 0 | 1 | Document trouvé, attribution non établie |
| R16 | 16 rue Pierre-Bérégovoy, La Roche-sur-Yon | 11 + 0 | 11 | Documents trouvés, attribution non établie |

**Contrôles acquis :** 14/14 comptes identiques lorsque le BAN est identique ; 157 projections documentaires identiques sur type, surface, classe, date d'établissement et date de fin de validité. Comparaison en multiensemble pour conserver les doublons de valeurs ; les six pages R14 sont incluses. Les 13 inventaires positifs retournent tous `unassigned`.

Cela prouve la parité de ces champs sur ces entrées. Cela **ne prouve ni l'identité de chaque numéro dans l'API**, qui le masque, **ni l'appartenance physique à un logement**, ni la complétude de la source nationale. Aucun taux global de précision ou de rappel ne peut être déduit de cette sélection orientée vers les cas intéressants.

## Références source des cas déterminants

- R06 : [ligne ADEME 2644E0008262R](https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines?qs=numero_dpe%3A%222644E0008262R%22&size=1) — adresse brute et BAN au 105, maison 79 m², classe D. L'API documentaire restitue ces valeurs et une validité calendaire non expirée ; ce n'est pas une certification de tout le cycle de vie officiel.
- R08/R09 : [ligne ADEME 2644E0031728F](https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines?qs=numero_dpe%3A%222644E0031728F%22&size=1) — brut 111, BAN 113, même diagnostic. L'adresse physique correcte reste à établir.
- R10/R11 : [ligne ADEME 2244E0138923Y](https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines?qs=numero_dpe%3A%222244E0138923Y%22&size=1) — brut 38 Alexandre-Huchon, BAN 21 Benoît-Chupiet. Ne pas conclure que les 19 documents du BAN concernent le 38.
- R12 : [ligne ADEME 2544E3389719R](https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines?qs=numero_dpe%3A%222544E3389719R%22&size=1) — référence remplacée `2544E3369721N`. Le détail DataCommune signale `missing_target`. L'existence du prédécesseur dans un autre périmètre source n'a pas été vérifiée dans cette série.

Les liens pointent vers une source vivante susceptible d'évoluer ; les réponses datées et leurs empreintes sont conservées dans `evidence/`.

## Le cas Rio : conclusion exacte

La clé `44143_3270_00002` ne retourne aucun document dans les deux datasets actuels interrogés, et DataCommune n'en retourne aucun. Une recherche complémentaire antérieure dans la même session a examiné les variantes de texte contenant Rio dans Rezé et le code postal 44400 : les résultats complets récupérés ne contenaient pas le mot de rue Rio. Les captures sont conservées dans `C:/tmp/datacommune-rio-diagnostic-20260910`.

Une [convention publiée par la commune](https://reze.inovawork.net/wp-content/uploads/2024/04/2024_04_11-Renouvellement-de-la-convention-pluriannuelle-dobjectifs-et-de-moyens-avec-lassociation-O.M.S.pdf) mentionne l'Office municipal des sports au 2 rue Rio. C'est un indice d'usage non résidentiel, pas une preuve qu'aucun logement ou DPE n'existe à cette adresse.

**Verdict :** pas de document source retrouvé par ces méthodes. L'audit ne prouve pas une absence de DPE dans les archives, le tertiaire, sous une autre adresse ou dans un document non publié. Rio ne doit pas devenir un test exigeant un DPE inventé. Le test attendu est une recherche étendue traçable et une voie de recherche par numéro.

## Référence concurrente et référentiels externes

La [carte Hello Watt](https://www.hellowatt.fr/renovation/diagnostic-performance-energetique/observatoire) a été ouverte dans le navigateur et la recherche R06 a été saisie puis validée. Le champ était rempli, mais aucun résultat individuel exploitable ni proposition d'adresse n'a été obtenu dans cette session. **Benchmark individuel non conclu ; aucune supériorité ou erreur de ce concurrent n'est déduite.** Son [communiqué de lancement](https://cdn.hellowatt.fr/media/model_files/Hello_Watt_lance_une_carte_pour_retrouver_son_DPE_Juin_2023.pdf) présente un parcours simple fondé sur les données ADEME et distingue une simulation lorsqu'aucun DPE n'est disponible. Il documente une promesse produit, pas son algorithme actuel.

La [méthode BDNB](https://www.bdnb.io/documentation/methode_traitement_dpe/) distingue un diagnostic représentatif du bâtiment du diagnostic de chaque appartement. Cette distinction justifie de conserver les différents niveaux dans le modèle et de ne pas transformer une donnée de bâtiment en DPE certain de chaque logement.

Sources de référence à conserver dans la conception : [ADEME logements existants](https://data.ademe.fr/datasets/dpe03existant), [ADEME logements neufs](https://data.ademe.fr/datasets/dpe02neuf), [données publiques de l'Observatoire](https://observatoire-dpe-audit.ademe.fr/donnees-dpe-publiques), [RNB](https://rnb.beta.gouv.fr/). Les règles de validité ou d'attestation doivent être vérifiées sur les publications officielles au moment de leur implémentation ; le présent audit n'en invente pas une nouvelle.

## Rejouer et vérifier

`probe.py` utilise seulement la bibliothèque standard Python. Les accès préproduction sont lus dans les variables d'environnement `AUDIT_DC_API_KEY`, `AUDIT_CF_ID`, `AUDIT_CF_SECRET`, jamais inscrits dans le script ou imprimés.

```powershell
python docs/audit-recentrage-2026-09-10/probe.py run --cases docs/audit-recentrage-2026-09-10/cases.json --out C:/tmp/datacommune-recentrage-replay/live
python docs/audit-recentrage-2026-09-10/probe.py details --cases docs/audit-recentrage-2026-09-10/cases.json --out C:/tmp/datacommune-recentrage-replay/details
```

Le contrôle ci-dessous vérifie **l'instantané de référence daté**. Il fixe volontairement les effectifs et le SHA : une nouvelle publication ou mise à jour des sources peut le faire échouer et demande une analyse, pas une modification automatique des attendus.

```powershell
python docs/audit-recentrage-2026-09-10/check_evidence.py docs/audit-recentrage-2026-09-10/evidence
```

Le contrôle confirme les mesures et affiche séparément les écarts produit. Son succès ne dit pas « DataCommune remplit déjà l'objectif utilisateur ». Les tests d'acceptation cible sont décrits dans les lots du plan ; ils ne sont pas prétendus exécutés.

Le manifeste contrôle les 25 captures JSON, y compris les quatre consultations détaillées. Les empreintes détectent une altération par rapport à ce manifeste ; elles ne sont pas une signature de l'ADEME ni un horodatage externe certifié.
