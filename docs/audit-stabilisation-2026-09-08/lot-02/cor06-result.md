# COR-06 — Contrat des loyers prédits

## Périmètre et correction

Travail isolé dans `C:/tmp/datacommune-stab-cor06-20260908`, base Git `aa9bece2332c516f318f3c65090e0101d2f4cd3d`. Aucun déploiement, push, accès mutable partagé ou changement du gate 58.2. Planning, workflow, OpenAPI généré et registre des champs restent à l'intégrateur.

Le montant est un **loyer d'annonce prédit, charges comprises**, pour un bien de référence non meublé, au T3 2025. Ce n'est ni une médiane observée, ni un loyer hors charges. Le transformateur conserve `id_zone`, `TYPPRED`, les observations commune et maille séparément, le R² ajusté, les bornes de prédiction et les caractéristiques de référence. Il refuse un type de fichier inconnu ou un nouveau millésime non revu.

Migration `092_loyers_prediction_metadata`, parent `091_dpe_source_lifecycle` : dix colonnes ajoutées sans DEFAULT ni backfill (`id_zone`, `type_prediction`, `nb_observations_commune`, `nb_observations_maille`, `r2_ajuste`, `trimestre_reference`, `charges_comprises`, `meuble`, `surface_reference_m2`, `surface_piece_reference_m2`). Les anciennes lignes 2023 **et** 2025 restent NULL pour ces colonnes. Les champs historiques sont conservés pour compatibilité : `nb_observations` correspond à la commune ; `r2` reste arrondi au millième ; `r2_ajuste` conserve la précision source en NUMERIC. Le montant et les bornes gardent leur précision historique de deux décimales, testée explicitement.

Les nouveaux objets loyers de `/v1/commune` viennent de la vraie projection SQL de la vue matérialisée ; ils correspondent aux réponses de `/v1/loyers`. Le mapper, les explications, le rendu Svelte et l'export CSV restituent référence, maille et prudence. Les anciennes données affichent des précisions inconnues. Les lignes textuelles portent `kind: 'context'` et n'augmentent pas le compteur d'indicateurs ; les lignes métriques restent soumises à `fieldId` et `state`.

## Sources figées et contre-exemples

- Guide existant de l'audit réutilisé : `parcours-loyers-guide-source.pdf`, SHA256 `620db206f562be7696e7abe2074dc4c8cd53392ff29dda16aae498df2e28a224`, 203461 octets, collecte attestée dans `parcours-loyers-source.json` au checkout principal. Aucun nouvel audit général.
- Guide p. 1 : T3 2025 et surfaces de référence (appartement 52 / 22,2 m² par pièce ; maison 92 / 22,4 ; T1-T2 37 / 22,9 ; T3+ 72 / 21,3), non meublé et charges comprises.
- Guide p. 2 : prudence pour moins de 30 observations communales ou R² inférieur à 0,5 ; changements méthodologiques interdisant d'en déduire une évolution inter-cartes. Aucun nouveau seuil de largeur d'intervalle inventé.
- Guide p. 3 : intervalle de prédiction à 95 %, `id_zone`, type de prédiction, deux effectifs et R² ajusté.
- Publication officielle : <https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025>.
- `cor06-sources/manifest.json` fige URL demandée/résolue, ressource, date UTC, taille et SHA256 des quatre préfixes CSV officiels. Lecture plafonnée à 131072 octets par fichier, arrêt du flux même lorsque le serveur ignore Range (HTTP 200), dernière ligne incomplète retirée. Aucune collecte nationale complète.
- `api/backend/tests/fixtures/loyers/official-2025.json` fournit huit témoins, avec ligne exacte dans le préfixe, hash et valeurs source indépendantes. Deux lignes par type : commune et maille. La Haute-Beaume (05066) conserve **0** observation communale et **484** observations de maille ; l'écart avec la généralité « maille ≥ 500 » du guide n'est pas corrigé artificiellement. Le cas EPCI est un témoin **synthétique**, pas une prétendue ligne officielle collectée.

## Preuves

| Vérification | Résultat | Trace |
|---|---|---|
| Contrat source avant correctif | 5 échecs discriminants (métadonnées perdues, type inconnu accepté) | `cor06-red.txt` |
| Source et contrats backend ciblés | 62 tests verts | `cor06-backend.txt`, `cor06-backend-final.txt` |
| Upgrade 091→092, historique NULL, downgrade local, 4 imports COPY/swap, endpoints ASGI réels | vert | `cor06-postgis.txt` |
| Ancien staging pré-092, puis correction et dry-run read-only | 1 échec rouge puis 17 tests verts (2 COR06 + 15 COR02) | `cor06-staging-red.txt`, `cor06-staging-green.txt` |
| Régressions publication COR04 et consommateurs DPE | 67 tests verts | `cor06-postgis-regression.txt`, `cor06-postgis-regression-final.txt` |
| Mapper, vrai composant Theme, CSV, profondeurs et modules voisins | 88 tests verts / 4 fichiers, dont 6 COR06 | `cor06-frontend.txt` |
| Svelte check | 0 erreur, 0 avertissement | `cor06-svelte-check.txt` |

Les suites destructives ont été lancées **uniquement** par `api/backend/scripts/run_contract_integration.ps1`, qui crée un projet PostGIS jetable à port éphémère, construit son DSN local et détruit le projet dans `finally`. Les logs attestent le retrait des conteneurs/réseaux. Les avertissements FastAPI existants ne sont pas des échecs. Ruff passe sur les fichiers Python concernés ; `git diff --check` passe.

La revue croisée a révélé une ancienne `_loyers_staging_flat` à douze colonnes : `CREATE IF NOT EXISTS` la conservait puis COPY échouait sur `id_zone`. Le pipeline nettoie désormais cette table lors d'une création fraîche et d'un abandon ; le scope de checkpoint `loyers-prediction-metadata-v1:2025` invalide les anciennes générations. Le runner reste inchangé. Un test conserve les octets du checkpoint et les lignes anciennes en dry-run, puis vérifie l'invalidation, une vraie COPY de deux lignes et le nettoyage sur interruption normale. Seul le contrôle d'espace disque PostgreSQL est doublé dans ce scénario interrompu ; aucun succès de publication nationale n'est revendiqué par ce test.

Commandes de reproduction depuis `api/backend` :

```powershell
uv run python -m pytest tests/test_loyers_source_contract.py tests/test_etl_staging_index_contract.py tests/test_commune_source_mirror_contract.py -q
./scripts/run_contract_integration.ps1 tests/integration/test_loyers_source_contract.py tests/integration/test_etl_dry_run.py
./scripts/run_contract_integration.ps1 tests/integration/test_flat_publication.py tests/integration/test_dpe_consumer_population.py
```

Le runtime partagé du principal a été réutilisé avec cwd explicite du worktree et `UV_PROJECT_ENVIRONMENT` vers sa venv ; tests unitaires avec DSN désactivé sur `127.0.0.1:1`. Frontend : jonction node_modules vers le principal, configuration temporaire dérivée de vite.config avec `resolve.preserveSymlinks=true` et `test.globals=true` pour contourner la résolution du setup externe dans la jonction Windows ; **plugins Svelte et jsdom réels conservés**. Commande Vitest : `run --config .cor06.vite.config.ts src/lib/data/loyers-prediction.test.ts src/lib/data/commune-fiche.test.ts src/lib/data/explications.test.ts src/lib/data/marketing-datasets.test.ts`. Configuration temporaire retirée après vérification. SvelteKit sync/check avec clé publique factice locale `cor06-local-fixture` ; aucun secret réel. L'intégrateur vérifie aussi avec la configuration native du principal.

## Payload et limites de livraison

`cor06-api-fixture.json` contient `{loyers, communes}`, indexés par code INSEE : réponses HTTP entières `statut/donnees/meta` issues de la base réellement importée et des vrais endpoints. Copie frontend identique dans `src/lib/data/__fixtures__/loyers-cor06-api.json` pour le rendu et l'export ; aucune substitution de la frontière SQL par un dictionnaire manuel.

L'URL du catalogue décrit la publication courante 2025 ; elle ne prouve pas la provenance d'une ligne historique 2023. Les anciennes métadonnées NULL et leur réserve UI restent nécessaires. **Réimport depuis source attestée et reconstruction des vues dépendantes dus avant de disposer de ces précisions sur les données existantes** ; aucune de ces opérations n'a été effectuée sur une base partagée. La migration seule ne qualifie pas rétroactivement les lignes et ne rafraîchit pas une vue déjà matérialisée.

Ce lot prouve les témoins collectés et les mécanismes locaux, pas la couverture nationale, une mise en production, l'ensemble des millésimes ou des tendances temporelles. Gate 58.2 reste bloquant.
