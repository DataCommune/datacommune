# Revue ciblée du plan 63-01

## Verdict après corrections

**Plan exécutable.** Les deux blocages de la première revue sont résolus dans `63-01-PLAN.md` et `63-VALIDATION.md` : l'intégration passe désormais par le wrapper PostGIS supporté et la preuve HTTP réutilise le client ASGI in-process existant, sans serveur ni script supplémentaire. Le contrat V2/V3, les contre-preuves et la portée partielle sont suffisamment précis pour démarrer l'exécution.

Ce verdict porte uniquement sur la qualité et la faisabilité du plan. Aucun test de l'implémentation V3 n'a été exécuté, aucune phase n'est validée et aucune preuve de déploiement n'est acquise.

## Points conformes

- Le défaut et `version=2` préservent strictement `DpeConsultedDocument` V2 ; `version=3` est opt-in sur `GET /v1/bien/dpe/document`; les autres valeurs rendent 422. Le contrat ne modifie ni inventaire ni Resolve (`CONTRAT-DPE-DOCUMENT.md`, sections « Route et compatibilité » et « Objet ajouté » ; `63-01-PLAN.md:23-28`, `:74-78`).
- `source_identity` est fermé : référence issue de `record.dpe_number`, jeux limités à `dpe03existant | dpe02neuf`, URLs constantes de catalogue. Il ne publie aucune adresse, clé BAN/RNB, observation interne ou URL libre (`CONTRAT-DPE-DOCUMENT.md`, « Objet ajouté » et « Ce qui ne change pas »).
- Le multi-régime et l'inconnu sont définis sans élection arbitraire : ordre existant puis neuf, déduplication, `[]` si inconnu. Le code actuel possède déjà le calcul validé des régimes à partir des observations, avec repli legacy (`api/backend/app/dpe/inventory_service.py:159-181`).
- La tombstone conserve déjà `dpe_number`, `regime_dpe` et `source_observations` pendant la minimisation, tout en purgeant les descriptifs (`api/backend/app/dpe/inventory_service.py:93-132`). `source_identity` est donc faisable sans nouvelle colonne. Le test doit prouver que la référence et les sources survivent, alors que tous les champs descriptifs V2 restent `null`.
- Les frontières d'erreur restent exactes : 400 référence invalide, 409 génération/date périmée, 503 lecture/publication indisponible, 422 négociation invalide (`api/backend/app/logements/router.py:137-152`). Le contrat distingue correctement panne et absence.
- La confidentialité est proportionnée : seul le numéro ADEME est nouvellement public en V3 ; références HMAC, secrets, compléments et observations restent exclus des réponses, logs et reçus.
- Le plan dit explicitement que 63-01 ne clôt ni le livrable visible ni la phase (`63-01-PLAN.md:50-54`, `:127-131`).

## Blocages initiaux — résolus

### 1. Commande PostGIS — résolu

`uv run pytest tests/integration/test_dpe_document_publication_routes.py` n'est pas une preuve PostGIS. Sans `CONTRACT_TEST_DATABASE_URL`, la fixture appelle `pytest.skip` (`api/backend/tests/integration/conftest.py:43-56`). Le fichier précise que le wrapper PowerShell est l'unique entrée supportée (`:1-18`).

Le plan et la validation utilisent maintenant la commande exacte :

```powershell
cd api/backend
pwsh -NoProfile -File scripts/run_contract_integration.ps1 tests/integration/test_dpe_document_publication_routes.py
```

L'assertion « aucun skip comme succès » et le nombre de tests passés/skippés dans le reçu sont conservés. `.github/workflows/ci-api.yml:93-103` exclut bien `tests/integration`, mais le dépôt possède une seconde voie : `.github/workflows/ci-data-contracts.yml` déclenche sur `api/backend/**`, `api/backend/app/dpe/**` et `api/backend/tests/integration/**` (`:22-64`), puis son job `postgis-integration` appelle le wrapper jetable et inclut précisément `test_dpe_document_publication_routes.py` (`:159-196`). Le plan peut donc réutiliser ce raccord sans modifier le workflow. Son exécution sur le futur candidat restera une preuve CI distincte, encore non acquise au stade de la revue.

### 2. Serveur/harness implicite — résolu

La version initiale de la tâche 3 demandait un script standard-library exécuté contre « l'API locale et les fixtures PostGIS R06/R04 ». Une fixture pytest in-process ne fournissait pas de port HTTP à ce processus externe. Rendre cela réel aurait demandé un orchestrateur qui démarre PostGIS, migre/seed la base, démarre Uvicorn avec les overrides d'auth/publication, exécute le client puis nettoie. Ce coût n'apportait pas une preuve supérieure pour le contrat demandé.

Le plan réutilise désormais le chemin déjà éprouvé dans `tests/integration/test_dpe_document_publication_routes.py` : la fixture `real_documentary_app` branche FastAPI sur le PostGIS isolé, et `client` traverse la frontière HTTP via `httpx.AsyncClient`/ASGI. R06 et R04 deviennent des lignes de fixture portant exactement leurs numéros/régimes/valeurs. C'est un client HTTP indépendant du frontend, sans socket ni serveur supplémentaire.

La correction appliquée est conforme :

- `api/backend/scripts/verify_dpe_document.py` a disparu des `files_modified`, artifacts et tâches ;
- la preuve bout en bout est placée dans le test d'intégration ASGI/PostGIS déjà prévu ;
- garder `docs/api/dpe-document.md` pour documenter des appels HTTP reproductibles et les réponses V2/V3 ;
- utiliser `docs/audit-recentrage-2026-09-10/socle/source-references.json` comme preuve source figée. Aucun appel ADEME ne doit faire partie du test local.

Le contrôle externe par numéro et le contrôle DataCommune local prouvent deux faits distincts : le JSON figé prouve que R06/R04 ont été retrouvés chez le producteur à la date de l'audit ; l'intégration locale prouve que l'API expose la même référence depuis le même enregistrement. Ne pas annoncer une nouvelle vérification ADEME si aucun appel distant n'est exécuté.

## Précisions intégrées au plan

### Union discriminée V2/V3

Le plan exige maintenant une union Pydantic/FastAPI explicitement discriminée par `version`, deux modèles fermés et le contrôle OpenAPI des deux variantes (`63-01-PLAN.md`, feature et tâche 1). Les contrôles V2 par défaut, V2 explicite et V3 restent à implémenter et exécuter ; leur définition est suffisante.

### Source validée, y compris legacy

Le plan impose maintenant un helper privé unique pour les régimes validés, utilisé par lifecycle et V3, y compris lorsque lifecycle est `None`. Cela répond au cas réel où `_lifecycle()` peut retourner `None` malgré un `regime_dpe` legacy reconnu (`api/backend/app/dpe/inventory_service.py:249-277`). Une observation incohérente reste fermée.

Tester au minimum : observations existant+neuf dans l'ordre inverse et avec répétition → deux sources ordonnées/dédupliquées ; observations absentes + `regime_dpe=existant` → existant ; régime inconnu → `[]` ; observation invalide → aucun contenu privé et comportement fermé conforme au contrat actuel ; tombstone multi-source → identité conservée, descriptifs nuls.

### Erreurs et publication

La tâche 1 demande maintenant explicitement `version=3` pour la référence étrangère, l'ancien contexte après swap attendu 409 et les publications `failed/wrong_oid` attendues 503 sans appel du service. Elle ferme donc les lacunes relevées dans le test existant (`api/backend/tests/integration/test_dpe_document_publication_routes.py:357`, `:494`).

Pour `test_document_routes_enforce_real_publication_sql`, transmettre `version=3` sur la route document et vérifier que le service n'est jamais appelé lorsque la publication est refusée (`api/backend/tests/integration/test_dpe_document_publication_routes.py:494`). L'absence reste un état de l'inventaire, pas une réponse documentaire vide.

## Commandes corrigées

Depuis `api/backend` :

```powershell
uv run pytest tests/test_dpe_inventory_service.py tests/test_dpe_inventory_privacy.py tests/test_dpe_inventory_endpoint.py tests/test_dpe_inventory_lifecycle_contract.py
pwsh -NoProfile -File scripts/run_contract_integration.ps1 tests/integration/test_dpe_document_publication_routes.py
uv run ruff check app/dpe/schemas.py app/dpe/inventory_service.py app/logements/router.py tests/test_dpe_inventory_service.py tests/test_dpe_inventory_privacy.py tests/test_dpe_inventory_endpoint.py tests/test_dpe_inventory_lifecycle_contract.py tests/integration/test_dpe_document_publication_routes.py
uv run ruff format --check app/dpe/schemas.py app/dpe/inventory_service.py app/logements/router.py tests/test_dpe_inventory_service.py tests/test_dpe_inventory_privacy.py tests/test_dpe_inventory_endpoint.py tests/test_dpe_inventory_lifecycle_contract.py tests/integration/test_dpe_document_publication_routes.py
```

Depuis la racine, preuve source figée :

```powershell
python docs/audit-recentrage-2026-09-10/check_evidence.py docs/audit-recentrage-2026-09-10/evidence
```

`check_evidence.py` valide le corpus d'audit général ; si `socle/source-references.json` n'est pas couvert par son manifeste, ajouter un contrôle local ciblé de forme/empreinte au test ou au reçu. Ne pas laisser entendre que cette commande vérifie automatiquement ce fichier sans assertion correspondante.

## Réserves non bloquantes

La menace T-63-03 conserve la phrase « client sans redirection authentifiée vers un autre hôte », héritée du client externe supprimé. Le parcours ASGI ne suit aucune redirection réseau et le service ne contacte pas l'ADEME ; cette phrase est désormais sans objet. La retirer clarifierait le threat model, sans empêcher l'exécution.

Le terme « client indépendant » signifie ici indépendant du frontend et traversant la frontière HTTP FastAPI via ASGITransport. Le plan le qualifie bien comme local in-process et ne prétend ni socket réseau ni serveur déployé.

La validation doit distinguer les deux workflows : `ci-api.yml` couvre la suite API sans intégrations, tandis que `ci-data-contracts.yml` porte le contrôle PostGIS et est déclenché par les fichiers du plan. Dire seulement « la CI API exclut les intégrations » est incomplet au niveau du dépôt ; dire que le contrôle est déjà exécuté sur le candidat serait également faux avant le run. Cette précision documentaire n'impose aucun changement de workflow.

## Critère de passage final

Le plan peut passer en exécution : wrapper PostGIS exact, raccord existant dans `ci-data-contracts.yml`, preuve HTTP ASGI/PostGIS sans infrastructure nouvelle, union discriminée prévue, dérivation multi-source/legacy unique, refus V3 couvrant contexte/génération/publication, confidentialité conservée et preuves locale/source séparées. `63-VALIDATION.md` marque tous les contrôles « À exécuter » et ne transforme pas cette revue en validation. Le résultat attendu reste une étape API de 63-01 vérifiable localement puis en CI ; la phase 63 demeure partiellement planifiée.
