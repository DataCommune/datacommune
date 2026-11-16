# Socle API — premier contrat documentaire DPE

## Décision proposée

Le premier livrable doit rendre un diagnostic ADEME **consultable et vérifiable comme document source**, même lorsque son rattachement à un logement n'est pas prouvé. Il étend le détail `DpeConsultedDocument` et conserve l'inventaire comme liste de documents connus à une adresse BAN. Il n'ajoute ni matching, ni choix automatique, ni statut d'attribution positif.

Le chemin existant est suffisant :

- `GET /v1/bien/dpe/inventaire?contexte=…&curseur=…&limite=…` reste la page bornée de références opaques ;
- `GET /v1/bien/dpe/document?contexte=…&reference=…` devient le contrat documentaire enrichi ;
- les deux routes restent authentifiées par la dépendance du routeur `logements`, `Cache-Control: no-store`, et relisent le document sous la cible signée (`api/backend/app/logements/router.py:155-184`, `:187-214`; `api/backend/app/dpe/queries.py:316-336`).

Ne pas créer de route de recherche par texte ou d'adresse brute dans ce lot. La consultation par numéro ADEME pourra être une évolution séparée, avec une finalité et des règles d'accès propres ; le présent livrable donne déjà au client le numéro nécessaire pour vérifier le document auprès de la source.

## Contrat public minimal

Ajouter au **détail uniquement** :

| Champ API proposé | Origine | Forme / règle | Finalité |
|---|---|---|---|
| `source_reference` | `diagnostics_dpe.numero_dpe` | chaîne non vide ; numéro ADEME, y compris sur une trace réconciliée | identifier le document officiel |
| `source_dataset` | observation cohérente `source_slug`, sinon `regime_dpe` | enum `dpe03existant | dpe02neuf | unknown` | nommer la source exacte sans exposer les observations internes |
| `source_url` | construit côté serveur depuis `source_dataset` + `source_reference` | URL HTTPS Data ADEME, paramètres encodés ; `null` si source inconnue | vérification directe |
| `source_address.raw` | `adresse_brut`, avec repli éventuel documenté vers `adresse_complete_brut` | `string | null`, normalisation d'affichage seulement | voir le libellé déclaré à la source |
| `source_address.ban.label` | `adresse_ban` | `string | null` | voir le résultat de géocodage ADEME |
| `source_address.ban.id` | `identifiant_ban` | **ne pas publier dans le minimum** | clé technique corrélée, inutile pour vérifier visuellement |

La structure imbriquée `source_address` évite de confondre adresse déclarée et adresse BAN. Elle doit porter une réserve stable : ces adresses décrivent le document source et son géocodage ; elles ne prouvent pas que le diagnostic concerne le logement consulté.

`source_reference`, `source_dataset` et `source_url` peuvent aussi être ajoutés aux items d'inventaire si le produit veut une liste immédiatement vérifiable. Le minimum plus prudent les garde dans `DpeConsultedDocument` : l'utilisateur déclenche explicitement « Consulter », la réponse reste bornée à un document et l'adresse brute n'est jamais propagée dans l'enveloppe Resolve/SSR initiale. Le numéro ADEME dans le détail est néanmoins requis dès ce livrable.

Ne pas exposer par défaut `adresse_complete_brut`, `nom_commune_brut`, `code_postal_brut`, `numero_voie_ban`, `nom_rue_ban`, `statut_geocodage`, `numero_etage_appartement`, `complement_adresse_logement`, `id_rnb`, `source_observations`, `revision_evidence` ou les identifiants SQL. Plusieurs sont libres, redondants, techniques ou susceptibles de contenir un complément fin. Leur utilité doit être démontrée champ par champ avant publication.

## Cartographie de bout en bout

### SQL et cycle de vie

La base possède déjà la matière : `numero_dpe` depuis la table initiale (`api/backend/migrations/versions/006_dpe_diagnostics.py:26-28`), la clé BAN depuis la migration 075 (`api/backend/migrations/versions/075_dpe_logement_fields.py:49-50`), les huit champs d'adresse brute/BAN et `regime_dpe` depuis la migration 090 (`api/backend/migrations/versions/090_dpe_adresse_brute_ademe.py:55-66`), puis `source_state`, snapshot, tombstone et faits de cycle de vie depuis la migration 091 (`api/backend/migrations/versions/091_dpe_source_lifecycle.py:131-148`). Le pipeline alimente notamment `adresse_brut` et `adresse_ban` (`api/backend/app/etl_dpe/pipeline.py:711-722`, `:875-957`).

La requête documentaire sélectionne aujourd'hui le numéro, les dates, le régime et le cycle de vie, mais pas les adresses (`api/backend/app/dpe/queries.py:64-89`). Le changement prévu est ciblé : ajouter `adresse_brut`, `adresse_complete_brut`, `adresse_ban` et, si le contrat retient l'ID structuré plus tard, `identifiant_ban` à `_INVENTORY_COLUMNS`/`DpeInventoryRecord`. La rellecture reste contrainte par `id + identifiant_ban + département` (`api/backend/app/dpe/queries.py:316-336`). Aucun prédicat de rapprochement n'est ajouté.

Les tombstones doivent rester minimales : la contrainte DB efface déjà toutes les adresses descriptives mais conserve nécessairement le numéro de document (`api/backend/migrations/versions/091_dpe_source_lifecycle.py:149-165`). Pour une trace, publier la référence et le cycle de vie ; rendre `source_address = null`. Ne jamais reconstruire une ancienne adresse depuis des observations ou logs.

### Pydantic et API

`DpeInventoryItem` est une allowlist fermée (`api/backend/app/dpe/schemas.py:387-408`) ; `DpeConsultedDocument` est une allowlist distincte et plus riche (`api/backend/app/dpe/schemas.py:473-505`). Ajouter les nouveaux champs à cette seconde classe, puis les projeter explicitement dans `load_dpe_document`, au point où le numéro est actuellement omis (`api/backend/app/dpe/inventory_service.py:557-580`).

Préserver sans changement :

- `version: 2` si l'ajout est traité comme extension additive ; si des consommateurs exigent une forme strictement exacte hors client officiel, publier `version: 3` est le choix sûr à arbitrer avant code ;
- `association = source_ban_equality`, `address_check = unverified`, `source = ADEME`, `source_coverage = unknown`, `graph_state`, `imported_at` (`api/backend/app/dpe/schemas.py:411-421`) ;
- tous les états `ready | absent | identity_unavailable | unavailable`, leurs invariants de total et l'absence de contexte sur identité indisponible (`api/backend/app/dpe/schemas.py:424-469`) ;
- lifecycle, tombstone, validité et absence de « document courant ».

Le modèle doit conserver `extra="forbid"` à l'entrée serveur. Les erreurs de validation et de lecture restent transformées en messages constants : 400 référence invalide, 409 inventaire à rafraîchir, 503 indisponibilité (`api/backend/app/logements/router.py:137-152`).

### TypeScript, SSR et rendu

Le client connaît les routes fixes et projette localement une allowlist stricte (`frontend/src/lib/api/dpe-inventory.ts:3-4`, `:62-111`, `:595-634`, `:658-705`). Ajouter les types imbriqués et la validation aux seuls `DpeConsultedDocument`; laisser `projectDpeInventoryPage` inchangé. Cela maintient le HTML SSR initial minimisé et empêche une réponse hostile d'injecter des champs libres dans Resolve.

Le composant de détail est le point de rendu naturel (`frontend/src/lib/components/fiche/DpeDocument.svelte:1-32`, `:118-125`). Afficher : « Référence ADEME … », un lien externe nommé vers la source, puis deux lignes distinctes « Adresse déclarée dans le document » et « Adresse issue du géocodage ADEME ». Garder la réserve `address_check` visible. La table reste « Documents DPE connus à cette adresse » avec son bouton `Consulter` (`frontend/src/lib/components/fiche/DpeInventory.svelte:91-137`) ; ne pas renommer une ligne en DPE du logement.

## Droits et minimisation

Le contrat réutilise les contrôles actuels : authentification/quota du routeur, références HMAC courtes, contexte lié à la génération, relecture exacte, réponses sans cache et erreurs non réfléchissantes. Le numéro DPE et le lien ADEME ont une finalité documentaire claire. Les deux libellés d'adresse sont utiles uniquement pour rendre visibles les contradictions R09/R11 ; ils doivent rester dans le détail à la demande, sans indexation, journalisation, analytics, URL, attribut HTML caché ou enveloppe SSR initiale.

Le lien officiel doit être construit depuis un enum fermé, jamais depuis une URL libre en base. `source_observations` permet de déterminer les slugs autorisés (`dpe03existant`, `dpe02neuf`) et le code sait déjà les convertir en régimes (`api/backend/app/dpe/inventory_service.py:168-172`; `api/backend/app/etl_dpe/sources.py:79-83`). Une incohérence source doit fermer le groupe comme aujourd'hui, pas produire un lien approximatif.

## Parcours client API hors frontend

1. Le client appelle `/v1/resolve?...&datasets=dpe` ou `/v1/logements/...` et récupère `dpe_inventory` avec un `context_ref` opaque lorsqu'une identité BAN est disponible.
2. Il appelle `GET /v1/bien/dpe/inventaire?contexte={context_ref}&limite=20`. Il parcourt `next_cursor` tant que `has_more=true`; `total` compte documents et traces, pas logements.
3. Pour un item, il appelle `GET /v1/bien/dpe/document?contexte={context_ref}&reference={document_ref}`. La réponse fournit la référence ADEME, la source et, si présentes, les deux adresses qualifiées. Le client ne déduit aucune attribution.
4. `state=absent` avec `total=0` signifie : zéro document dans l'inventaire exact-BAN lu. Cela ne signifie pas qu'aucun DPE n'existe dans la source nationale.
5. `state=identity_unavailable` signifie : aucune cible BAN canonique utilisable ; le corpus n'a pas été lu.
6. `state=unavailable`, HTTP 503, timeout ou transport en échec signifie : panne/lecture indéterminée. Le client doit permettre de réessayer et ne jamais convertir ce cas en liste vide.
7. HTTP 409 signifie que les références ont expiré par génération/date : reprendre depuis Resolve pour obtenir un nouveau contexte. HTTP 400 signifie référence invalide ; ne pas réessayer à l'identique.

R09/R11 ne seront pas retrouvés par ce seul contrat à partir du mauvais BAN : c'est une limite assumée. Une future recherche source par numéro ou un census pré-BAN est nécessaire pour ces cas, sans changer l'inventaire exact-BAN ni prétendre un rattachement.

## Tests existants à adapter et contrôles ciblés

Les tests actuels verrouillent précisément l'ancienne exclusion ; ils doivent être modifiés intentionnellement, pas contournés :

- backend : `test_document_projection_is_closed_and_checked_after_session_close` (`api/backend/tests/test_dpe_inventory_service.py:391`), `test_private_source_fields_never_cross_http_or_readable_references` (`api/backend/tests/test_dpe_inventory_privacy.py:267`) et `test_openapi_exposes_only_fixed_opaque_navigation_contract` (`api/backend/tests/test_dpe_inventory_endpoint.py:288`) ;
- lifecycle/tombstone : `test_tombstone_rejects_old_content_on_both_public_shapes` (`api/backend/tests/test_dpe_inventory_lifecycle_contract.py:161`) ;
- SQL et publication : `test_real_sql_journey_preserves_candidates_without_attribution` et `test_real_sql_distinguishes_empty_inventory_from_invalid_publication` (`api/backend/tests/integration/test_dpe_document_publication_routes.py:219`, `:389`) ;
- client : projections fermées et routes (`frontend/src/lib/api/dpe-inventory.test.ts:211-224`, `:329-378`, `:483-537`) ;
- rendu/SSR/confidentialité : `frontend/src/lib/data/dpe-inventory-view.test.ts:330-402` et `frontend/tests/e2e/dpe-inventory.spec.ts:224-320`, avec panne/absence à `:407`.

Ajouter les assertions d'acceptation suivantes : R06 expose `2644E0008262R`, `dpe03existant`, une URL ADEME sûre, les libellés brut/BAN attendus, 79 m² et D, tout en restant `unassigned`; un document brut/BAN contradictoire affiche les deux valeurs sans les fusionner; un tombstone garde sa référence mais aucune adresse; une valeur libre contenant contrôle, saut de ligne ou URL ne devient jamais attribut/lien; les champs exclus restent absents de JSON, HTML SSR et logs; `absent` reste une réponse réussie explicite et une panne reste 503/`unavailable`.

Commandes locales réalisables depuis `C:/tmp/datacommune-phase591-20260910` :

```powershell
cd api/backend
uv run pytest tests/test_dpe_inventory_service.py tests/test_dpe_inventory_privacy.py tests/test_dpe_inventory_endpoint.py tests/test_dpe_inventory_lifecycle_contract.py
uv run ruff check app/dpe app/logements tests/test_dpe_inventory_service.py tests/test_dpe_inventory_privacy.py tests/test_dpe_inventory_endpoint.py tests/test_dpe_inventory_lifecycle_contract.py
uv run ruff format --check app/dpe app/logements tests/test_dpe_inventory_service.py tests/test_dpe_inventory_privacy.py tests/test_dpe_inventory_endpoint.py tests/test_dpe_inventory_lifecycle_contract.py
cd ../../frontend
npm run test:unit -- --run src/lib/api/dpe-inventory.test.ts src/lib/data/dpe-inventory-view.test.ts
npm run check
npx playwright test tests/e2e/dpe-inventory.spec.ts
```

Les intégrations SQL exigent PostgreSQL/PostGIS et la configuration de test du dépôt :

```powershell
cd api/backend
uv run pytest tests/integration/test_dpe_document_publication_routes.py
```

La preuve réelle datée peut être vérifiée sans appel distant :

```powershell
python docs/audit-recentrage-2026-09-10/check_evidence.py docs/audit-recentrage-2026-09-10/evidence
```

## Blockers et hypothèses à fermer avant implémentation

- **Version du contrat** : confirmer si les consommateurs tiers acceptent des champs additifs en V2. À défaut de preuve, versionner le détail en V3 ou documenter explicitement la compatibilité additive.
- **Adresse brute exacte** : décider si `adresse_brut` seul suffit ou si `adresse_complete_brut` est le libellé de repli. Ne pas concaténer les sous-champs libres sans règle testée.
- **URL ADEME canonique** : valider le format officiel durable pour chaque dataset au moment du code. Le dataset source est établi ; la stabilité d'une URL de ligne individuelle ne l'est pas encore contractuellement.
- **Droits produit** : l'audit n'établit pas d'interdiction légale du numéro ou des libellés publics, mais ne constitue pas une analyse juridique. La minimisation proposée reste le garde-fou technique.
- **Données existantes** : les colonnes sont présentes, mais les tombstones les purgent et certains imports antérieurs peuvent contenir `NULL`. Le contrat doit accepter ces absences sans fabriquer une valeur.
- **Couverture** : ce livrable rend vérifiables les documents déjà importés. Il ne résout pas les DPE éliminés avant import, les BAN contradictoires, Rio, ni l'identité logement.

La validation visée est locale. Aucun test distant, déploiement ou mesure de production n'est inclus dans ce plan.
