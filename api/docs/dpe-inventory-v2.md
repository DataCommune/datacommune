# Inventaire DPE V2

Ce document décrit le contrat public de consultation des DPE connus à une
adresse. Il s'applique aux réponses de version `2` et complète le snapshot
OpenAPI `contracts/openapi.v1.json`.

`/v1` reste une API bêta : la migration V2 modifie le vocabulaire DPE. Les
codes HTTP, `Retry-After` lorsqu'il est présent et le champ `status` des erreurs
RFC 9457 restent contractuels selon la
[politique de dépréciation](api-deprecation-policy.md).

## Principe fonctionnel

Trois objets sont volontairement séparés :

1. **L'inventaire à l'adresse** indique les documents DPE importés associés à
   la même clé BAN source.
2. **Le document consulté** expose le détail public d'une ligne choisie dans
   cet inventaire.
3. **L'attribution au logement** exprime une preuve d'identité distincte.

La présence d'un ou plusieurs DPE à l'adresse ne prouve donc pas qu'un document
appartient au logement recherché. Pour un inventaire lisible, le classifieur V2
renvoie `unassigned` avec la raison `dwelling_identity_unproven` quand des
documents existent, ou `absent` avec `not_reported` quand aucun document n'est
connu. Une identité source indisponible devient `absent/identity_unavailable` ;
une lecture indisponible devient `error/temporary_error`. Aucun diagnostic
n'est automatiquement promu en `exact` ou en `multiple`.

L'ancien endpoint de proximité `GET /v1/dpe` garde un contrat séparé. Son
résultat ne doit pas être converti en attribution au logement.

## Authentification et cache

Les deux routes passent par la protection habituelle de `/v1`. Le schéma public
déclaré dans l'OpenAPI est la clé `X-API-Key` ; la SPA peut aussi utiliser son
cookie de session JWT HttpOnly. Dans les deux cas, le quota du compte
s'applique. Toutes les réponses, y compris les erreurs de validation, portent
`Cache-Control: no-store`.

### Lire une page d'inventaire

```http
GET /v1/bien/dpe/inventaire?contexte=opaque-context-example&limite=20
```

| Paramètre | Obligatoire | Contrat |
|---|---:|---|
| `contexte` | oui | Référence opaque, de 1 à 512 caractères |
| `curseur` | non | Curseur opaque de la page suivante, de 1 à 512 caractères |
| `limite` | non | Entier de 1 à 50, valeur par défaut : 20 |

Exemple synthétique réduit :

```json
{
  "version": 2,
  "state": "ready",
  "context_ref": "opaque-context-example",
  "items": [
    {
      "document_ref": "opaque-document-001",
      "type_batiment": "appartement",
      "surface_habitable": 87.0,
      "etiquette_dpe": "D",
      "etiquette_ges": "B",
      "date_etablissement_dpe": "2024-01-15",
      "date_fin_validite_dpe": "2034-01-14"
    }
  ],
  "total": 137,
  "distribution": {
    "A": 2,
    "B": 8,
    "C": 25,
    "D": 48,
    "E": 31,
    "F": 15,
    "G": 6,
    "unknown": 2
  },
  "page_size": 20,
  "has_more": true,
  "next_cursor": "opaque-cursor-example",
  "read_state": "complete",
  "association": "source_ban_equality",
  "address_check": "unverified",
  "source": "ADEME",
  "source_coverage": "unknown",
  "graph_state": "not_checked",
  "imported_at": null
}
```

`total` compte les diagnostics importés sur l'adresse source, pas les
logements actuels. La distribution couvre le même snapshot et le même
périmètre ; la somme de `A` à `G` et `unknown` est égale au total confirmé.
Une page ne contient au plus que `page_size` lignes.

| `state` | Sens |
|---|---|
| `ready` | L'inventaire est lisible ; `context_ref` est présent. |
| `absent` | Lecture réussie et aucun document connu ; `total` vaut `0`. |
| `identity_unavailable` | L'adresse source exacte n'a pas pu être établie ; aucun contexte n'est émis. |
| `unavailable` | Lecture temporairement impossible ; aucun total n'est affirmé. |

La route fixe `/v1/bien/dpe/inventaire` reçoit nécessairement un contexte déjà
émis : une réponse 200 y est donc normalement `ready` ou `absent`, et une panne
de lecture devient HTTP 503. Les états `identity_unavailable` et `unavailable`
servent surtout aux inventaires embarqués dans `/v1/resolve` et
`/v1/bien/logements`, afin que ces réponses puissent conserver leurs autres
données.

`read_state` vaut `complete` ou `partial`. Une lecture partielle ne doit jamais
être interprétée comme un recensement exhaustif ni permettre une attribution.
`source_coverage: "unknown"` et `address_check: "unverified"` rappellent les
limites de couverture et d'association. Un `graph_state: "incoherent"` signale
une incohérence observée dans les liens de diagnostics ; il n'élit pas une
ligne de remplacement.

La première page peut aussi être incluse dans les réponses de `/v1/resolve` et
`/v1/bien/logements`. La pagination et la consultation utilisent ensuite les
deux routes fixes ci-dessus.

### Consulter un document

```http
GET /v1/bien/dpe/document?contexte=opaque-context-example&reference=opaque-document-001
```

`contexte` et `reference` sont obligatoires, opaques et limités à 512
caractères. La réponse contient toujours `version`, `context_ref`,
`document_ref`, la provenance et `validity_by_date`. Elle reprend les champs
publics de la ligne et contient aussi les champs métier suivants, avec `null`
lorsque la source ne les renseigne pas :

- l'année et la période de construction ;
- le type d'installation et l'énergie principale de chauffage ;
- la qualité d'isolation de l'enveloppe ;
- la consommation des cinq usages en **kWhEP/an** ;
- les émissions des cinq usages en **kgCO2eq/an** ;
- le coût total des cinq usages en **€/an** ;
- `validity_by_date` : `unknown`, `expired` ou `not_expired_by_date`.

`surface_habitable` est la surface habitable déclarée par le DPE, en m². Le
service ne la remplace pas par une surface DVF et ne fabrique aucun ratio par
m². Une valeur absente reste `null`.

`not_expired_by_date` signifie uniquement que la date de fin connue n'est pas
dépassée au jour de la lecture. Ce statut ne garantit ni l'absence d'un
diagnostic remplaçant, ni une attribution au logement.

## Références opaques et durée de vie

Les références de contexte, document et curseur sont signées, séparées par
finalité et valables au maximum **1 800 secondes**. Ce sont des jetons de
navigation temporaires soumis à l'authentification, au quota et aux contrôles
en base. Leur possession seule n'autorise aucune lecture. Ce ne sont ni des
identifiants métier ni des liens permanents.

Le serveur revalide la cible, les générations des tables actives et les
empreintes techniques lors de chaque lecture. Une référence peut donc devenir
obsolète après un import, un échange de table, une restauration, une rotation
de clé ou une réutilisation d'identifiant. Le client doit alors reprendre le
parcours depuis `/v1/resolve` ou `/v1/bien/logements` pour obtenir un nouveau
contexte.

Ne pas stocker ces références durablement, les écrire dans des journaux ou les
transformer en paramètre public d'une URL de fiche.

## Projection publique et confidentialité

Une ligne d'inventaire expose uniquement :

- `document_ref` ;
- `type_batiment` ;
- `surface_habitable` ;
- `etiquette_dpe` et `etiquette_ges` ;
- `date_etablissement_dpe` et `date_fin_validite_dpe`.

Le détail ajoute seulement les champs listés dans la section précédente. Le
numéro ADEME, l'identifiant BAN, l'adresse brute, les compléments libres,
l'étage, la porte, le bâtiment, les coordonnées, les identifiants de
remplacement et les documents individuels d'une autre adresse BAN ne font pas
partie du contrat public.

Les modèles sont fermés : un champ supplémentaire reçu ou produit est une
erreur de contrat, pas une extension implicite à recopier dans le DOM, un
export ou un journal.

## Erreurs et reprise client

Les erreurs sont servies en `application/problem+json`. Le texte humain peut
évoluer ; un client branche sa logique sur le code HTTP.

| Code | Traitement client |
|---:|---|
| `400` | Référence invalide, mauvaise finalité ou cible incohérente : abandonner la référence. |
| `409` | Contexte expiré ou obsolète : rafraîchir l'adresse puis reprendre avec les nouvelles références. |
| `422` | Paramètre absent ou hors bornes : corriger la requête. La valeur fautive n'est pas reflétée. |
| `503` | Lecture ou service DPE temporairement indisponible : conserver les données déjà acquises et réessayer plus tard. |

Les erreurs d'authentification et de quota gardent le contrat commun de l'API
(`401`, `403`, `429` et `Retry-After` le cas échéant).

## Migration des consommateurs

Un consommateur V2 doit :

1. exiger `version === 2` et refuser proprement une version inconnue ;
2. afficher l'inventaire et le document consulté sans les présenter comme le
   DPE du logement ;
3. utiliser `total`, `distribution`, `read_state` et `has_more` sans déduire le
   nombre de logements ;
4. traiter les références comme temporaires, renouveler le contexte sur `409`
   et jeter définitivement une référence rejetée en `400` ; si elle provenait
   d'un ancien parcours, redémarrer depuis `/v1/resolve` ou
   `/v1/bien/logements` ;
5. conserver les unités annuelles et les valeurs `null` ;
6. projeter explicitement l'allowlist publique, sans recopier une réponse
   source complète ;
7. ne plus utiliser `exact`, un candidat unique, `?dpe=` ou un résultat de
   proximité comme preuve automatique d'identité logement.
