# L11 — récupération locale du schéma 094 avec image construite

**Une voie locale est qualifiée : restaurer une sauvegarde 094, conserver le refus des lecteurs, puis revalider la source et republier/reconstruire avec le code compatible.** Le retour au service a été observé dans une image réellement construite depuis le Dockerfile de production. Aucun rollback vers `a5ea02f`, aucune sauvegarde ou image de production qualifiée.

## Résultat et frontières

Un parcours PostgreSQL réel **PASS, zéro skip, en 34,52 s**, avec source LOVAC complète figée de **34 912 communes**. Le conteneur PostgreSQL neuf `dc-contract-test-243200518d` et ses bases enfants ont été détruits par le wrapper ; les conteneurs lecteurs et le processus écrivain ont également été supprimés. Aucun DSN partagé, secret, accès réseau à une source ou fichier historique modifié.

Le code applicatif de référence est le commit `6b3e0aa54bc831c53a756ea5b2d2014411bc5264`, dans le worktree détaché `C:/tmp/datacommune-l11-recovery-20260908`. Les nouveaux tests et preuves constituent un changement distinct. L'image locale provient de `api/docker/Dockerfile` avec contexte `api/backend`, sans patch applicatif :

- ID utilisé et vérifié sur les conteneurs : `sha256:8e92b9ea916e6fe19270a6a78fbae005d41868076c045e849daba7655f244a2b`.
- Label runtime constaté : `org.datacommune.publication-contract=1`.
- **438 fichiers** Python applicatifs/migrations et fichiers `pyproject.toml`/`uv.lock` de l'image comparés octet par octet au checkout. Empreinte du manifeste : `4a5591971d8f6b065e5af1ee0127f6392e5114263d711caf5c5c335676da84e6`.
- Le label n'est pas la preuve de compatibilité : les lectures TCP/HTTP et l'écriture du journal décrites ci-dessous utilisent effectivement cette image par ID immuable.

## Séquence réellement exécutée

| Étape | Contrôle et résultat |
|---|---|
| Base neuve → migrations réelles jusqu'à 091 | Toutes les migrations appliquées ; ajout d'une sonde spatiale synthétique avec séquence |
| Sauvegarde 091 → destination neuve | `pg_dump --format=custom` et `pg_restore --exit-on-error` réels ; schéma, contenu et objets contrôlés identiques après canonicalisation expliquée plus bas |
| Source originale → migrations 092/093/094 | Chaîne réelle jusqu'à head ; aucune génération historique inventée |
| Publication LOVAC | Source figée complète → téléchargement local adapté → contrôles d'entête/transform/COPY/swap → ValidationHook → reconstruction réelle des vues → journal published |
| Image lectrice avant sauvegarde 094 | HTTP 200 sur LOVAC (Nantes, secret statistique, zéro mesuré) et Commune Nantes |
| Sauvegarde 094 → seconde destination neuve | Contenu et définitions comparés ; première ligne published conservée intégralement ; OID réellement différents |
| Image lectrice après restauration | HTTP **503**, aucun objet `donnees` pour les routes LOVAC, et 503 Commune ; le journal published aux anciens OID ne rend pas le stock recevable |
| Processus écrivain de la même image interrompu | `Publication.__enter__()` puis `observe_stock()` réels ; `os._exit(17)` sans nettoyage Python ; ligne durable pending et verrou libéré à la mort du processus ; lecteurs 503 |
| Reprise avec échec requis injecté | Vrai runner, vraie validation, interruption du rebuild → nouvelle ligne failed ; metadata de succès inchangée ; lecteurs 503 |
| Nouvelle reprise complète | Vrai runner repart, revalide, republie et reconstruit ; nouvelle ligne published et lecteurs **200** dans l'image |

La séquence finale du journal est **published, pending, failed, published**. Les lignes published initiale et pending du crash restent identiques : aucun UPDATE manuel d'OID ou de statut pour fabriquer la disponibilité.

Les OID `(vacance, v_commune_synthese)` passent de `(44451,44551)` avant sauvegarde à `(51270,51282)` après restauration, puis `(56999,57099)` après republication. Le changement a été constaté, sans décalage artificiel imposé.

## Données et objets contrôlés

- Après restauration 091 : 778 relations de stockage examinées, 13 136 descriptions de colonnes, 799 contraintes, 4 725 index, 10 définitions de vues matérialisées et 36 séquences.
- Après restauration 094 : **780 relations de stockage** (tables, partitions et vues matérialisées), **13 172 descriptions de colonnes**, **805 contraintes**, **4 732 index**, **10 définitions de vues matérialisées**, **37 séquences**.
- Chaque relation peuplée est comparée par nombre de lignes et empreinte déterministe du contenu JSON trié ; les vues non peuplées sont distinguées. L'empreinte globale avant/après restauration 094 est `2f033786a1fc721a9ad381da4c144e244873f870a321d8a058462046c703f99a`.
- Après récupération, les 34 912 tuples LOVAC sont confrontés au lecteur CSV/Decimal indépendant L09, et leur contenu intégral est comparé à la sauvegarde. Les trois témoins HTTP conservent valeurs, années, NULL et zéros. La sonde géométrique reste `POINT(-1 47)` et la séquence produit bien l'identifiant suivant `2`.
- Le DDL des autres datasets est réel mais leurs stocks restent vides. Trois ancrages démographiques synthétiques permettent la projection Commune ; leurs libellés/populations ne sont pas certifiés. Ce test n'est donc pas une restauration du contenu national de tous les datasets.

Les empreintes d'archives sont consignées dans [verification.json](verification.json) : 091 `3f458bcf28222d299e39383bb69276f27df8f537b1ae3d356c7c0d5bb2ee2cfa`, 094 `17b09f5f6d8292aab1c6eb61ecc9e7c8ffd5b4ca535009ac49d5d0a6432bb837`. Les archives étaient dans le conteneur jetable et ont été détruites avec lui ; aucun dump binaire n'est versionné ni présenté comme sauvegarde opérationnelle conservée.

## Adaptateurs explicites et limites

La [sonde HTTP](probe.py) lance Uvicorn avec les **vrais routers, modèles, requêtes, connexion DB et gardes** issus de l'image. Elle omet volontairement l'auth/quota et le lifespan de `app.main` ; elle ne prouve pas le démarrage ou la santé de l'application complète. Le chemin `/app` est ajouté au chargement de la sonde externe, car le remplacement de `--app-dir` par `/probe` retire le répertoire source habituel d'Uvicorn. Aucun code applicatif n'est monté depuis l'hôte.

Le processus de journalisation interrompu tourne dans l'image. Les publications complètes passent par le runner du checkout, avec les mêmes sources applicatives et dépendances du laboratoire ; le processus scheduler complet dans l'image n'est pas certifié. Adaptations du runner : acquisition réseau remplacée par les octets L09 figés, mesure disque issue du conteneur réel, une exception explicite du hook de reconstruction. Les gates de données, COPY, swap, validation, reprise fraîche, reconstruction et publication restent réels.

Le dump utilise `--no-owner --no-privileges` : propriétaires, rôles, ACL, paramètres serveur, tablespaces externes et extensions hors de ce laboratoire ne sont pas certifiés. Les objets comparés sont les catégories listées ci-dessus, pas toute propriété imaginable du catalogue. L'échelle et le temps mesurés ne sont ni un RTO/RPO ni un budget disque production.

## Essais de diagnostic conservés

Les deux premiers essais ont révélé une variation **de représentation PostgreSQL**, sans différence de contenu : deux contraintes et deux vues voient leurs casts de tableaux réécrits lors du dump/restore, de `(ARRAY['…'::varchar,…])::text[]` vers `ARRAY[('…'::varchar)::text,…]`. Le [diff des entrées modifiées](091-difference.json) conserve intégralement leurs valeurs avant/après ; les entrées identiques sont omises. Le comparateur ne canonicalise que ce motif exact à littéraux ; PostgreSQL vérifie lui-même l'équivalence des deux expressions avec `IS NOT DISTINCT FROM`. Toute autre différence reste bloquante. Il n'est pas affirmé que les définitions brutes sont octet-identiques.

Le troisième essai a identifié l'adaptateur `--app-dir /probe` manquant ; aucune modification de l'image ni de l'application n'a été nécessaire. [attempt1.log](attempt1.log), [attempt2.log](attempt2.log), [attempt3.log](attempt3.log). Un passage intermédiaire a précédé l'ajout du crash exécuté dans l'image et du contrôle des fichiers image ; seul [recovery.log](recovery.log) / [recovery.xml](recovery.xml) décrit le dernier parcours complet. Logs/XML normalisés en LF, espaces de fin de ligne retirés, sans omission de résultat. Ruff ciblé conforme.

## Reproduction locale et usage avant release

Depuis la racine du checkout exact à tester :

```powershell
docker build -f api/docker/Dockerfile -t datacommune-l11-recovery-local:20260908 api/backend
$env:L11_RECOVERY_IMAGE='datacommune-l11-recovery-local:20260908'
pwsh -NoProfile -File api/backend/scripts/run_contract_integration.ps1 tests/integration/test_l11_recovery_image.py --junitxml=../../docs/audit-stabilisation-2026-09-08/lot-11/recovery/recovery.xml --tb=short
```

Le wrapper choisit son conteneur, port loopback et DSN, puis détruit ses ressources. Le test exige une image explicitement construite et refuse un label absent ou un code différent. Il n'est pas inclus implicitement dans les sélections CI SQL existantes : une qualification CI doit d'abord construire son image candidate, fournir l'ID et exécuter cette sélection. Aucune CI distante n'a été exécutée ici.

Avant release restent concrets : obtenir les CI du candidat final ; relever une sauvegarde réelle récente et prouver sa restauration dans un environnement autorisé ; qualifier le runtime complet et son image de secours par ID ; arrêter/coordonner les producteurs lors d'une récupération ; identifier toutes les sources/générations à republier, mesurer espace et délais, puis vérifier SQL/HTTP/UI et observer le service. La restauration 091 ici prouve le retour de schéma/stock du laboratoire ; aucun ancien binaire complet n'a été construit pour en prouver le retour au service.

Le garde L10 doit rester actif : le prédécesseur `a5ea02f` n'est toujours pas une cible de rollback admissible après 094. Le chemin qualifié localement est **restauration 094 puis republication compatible**, pas downgrade, correction manuelle du journal ou contournement du garde. COR23 global, STAB-RELEASE et 58.2 ne sont pas clos par ce résultat.
