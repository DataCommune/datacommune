# Politique de dépréciation — API publique `/v1`

> Dossier d'origine : OWD-03 (audit réversibilité 2026-08-04, chantier C4).
> Cette politique est **résumée dans la bannière OpenAPI** (`app/main.py`, description) :
> c'est la version publiée que voient les clients sur `/docs`. Ce fichier est la version
> de référence interne ; les deux doivent rester cohérentes (test
> `tests/test_openapi_contract_governance.py`).

## 1. Statut du contrat : bêta publique

`/v1` est en **bêta publique**. Tant que ce statut est affiché dans la description
OpenAPI, la compatibilité ascendante du contrat (noms de champs, formes de réponse,
paramètres) **n'est pas garantie**. Le statut n'est levé que par une décision explicite,
datée, qui retire la bannière — jamais par accident ni par écoulement du temps.

Conséquence assumée : ce qui est publié **avec** avertissement peut évoluer ; ce qui
serait publié **sans** avertissement serait figé par accident (le problème OWD-03
d'origine). La bannière est donc un artefact de gouvernance, pas de la prose — elle est
gardée par un test.

## 2. Contrat d'erreur stable — fiable dès maintenant, même en bêta

Trois éléments des réponses d'erreur sont **contractuels** et le restent :

1. **Le code de statut HTTP** (401, 403, 404, 422, 429, 500, 502, 503…).
2. **L'en-tête `Retry-After`** — présent sur les 429 (rate limit, `app/rate_limit.py`)
   et les 503 (upstream d'un proxy indisponible, `app/common/proxy.py`). C'est lui qui
   dit au client quand réessayer.
3. **Le champ `status` du corps d'erreur** `application/problem+json` (RFC 9457,
   schéma `ProblemDetail` dans `app/common/schemas.py`).

Explicitement **non contractuels** : les textes `title` et `detail`. Ils sont en
français, destinés aux humains, et peuvent changer sans préavis — corriger une typo
n'est pas un breaking change. Un client qui parse ces textes s'est mis hors contrat.

## 3. Procédure de dépréciation (compatible RFC 8594)

Retirer ou renommer un élément publié suit toujours le pattern **expand/contract** :

1. **Expand** — publier le remplaçant (nouveau champ, nouveau paramètre, nouvel
   endpoint) à côté de l'ancien.
2. **Annoncer** — marquer l'ancien `deprecated: true` dans l'OpenAPI, et émettre sur
   ses réponses les en-têtes :
   - `Deprecation` (RFC 9745) — l'élément est déprécié, avec la date de la décision ;
   - `Sunset: <date HTTP>` (RFC 8594) — la date à partir de laquelle il peut être retiré.
3. **Coexister** — les deux formes répondent pendant tout le délai annoncé.
4. **Contract** — retirer l'ancien à la date `Sunset` ou après, jamais avant.

### Délais minimaux entre annonce (`Sunset`) et retrait

| Statut du contrat | Délai minimal |
|---|---|
| Bêta publique (statut actuel) | **30 jours** |
| Après sortie de bêta | **90 jours** |

Ces délais sont ce qui rend expand/contract **fini** : sans terme annoncé, les champs
dépréciés s'accumulent indéfiniment faute de savoir quand on a le droit de les retirer.

### État courant

**Dépréciations annoncées** (décision du 2026-10-06, plan 04-13). Les deux routes
répondent sans changement de comportement ; `deprecated: true` dans l'OpenAPI, et
chacune de leurs réponses — succès et erreurs, refus d'authentification et de
publication compris — porte les mêmes en-têtes constants :

| Route | Remplacement | `Deprecation` (RFC 9745) | `Sunset` (RFC 8594) |
|---|---|---|---|
| `/v1/bien/logements` | `/v1/adresse/{ban_id}` | `@1791244800` (2026-10-06 00:00 UTC) | `Wed, 06 Jan 2027 00:00:00 GMT` |
| `/v1/bien/dpe/document` | `/v1/dpe/{numero}` | `@1791244800` (2026-10-06 00:00 UTC) | `Wed, 06 Jan 2027 00:00:00 GMT` |

L'annonce n'atteint les clients qu'au déploiement : le `Sunset` laisse 92 jours après
la décision pour que le délai bêta de 30 jours reste tenu même avec un déploiement
tardif. Si le déploiement intervenait moins de 30 jours avant cette date, le `Sunset`
serait repoussé, jamais le délai raccourci. Le `Sunset` autorise un retrait, il ne
l'impose pas : aucun retrait n'a lieu sans décision explicite.

Restent non dépréciés : `/v1/bien/dpe/inventaire`, `/v1/bien/dpe/comparaison`
(outil d'administration) et `/v1/resolve`. Les en-têtes ne couvrent pas les réponses
produites hors de la route (429 du limiteur de débit, 500 non géré).

**Routes en quarantaine.** `/v1/encadrement-loyers`, `/v1/scoring/renov` et
`/v1/scoring/renov-target` sont marquées `deprecated` dans l'OpenAPI depuis leur mise
en quarantaine : elles ne répondent que 503 et ne portent ni `Deprecation` ni `Sunset`
(aucune date de retrait annoncée). Garde : `tests/test_openapi_contract_governance.py`.

## 4. Snapshot du contrat public (chantier C1 — livré)

Le contrat publié est **commité** dans `contracts/openapi.v1.json`. Seul writer :
`scripts/export_openapi.py` (`uv run python -m scripts.export_openapi` depuis
`api/backend`) — jamais d'édition manuelle, même gouvernance que le registre
sémantique. Gate de drift : `tests/test_openapi_snapshot.py` — tout changement de
code qui altère le contrat public fait échouer la CI tant que le snapshot n'est pas
régénéré et son diff relu. Le gate ne bloque pas le changement : il bloque le
changement **inaperçu**.

## 5. Hors périmètre de ce document

Restants du dossier OWD-03 (suivi interne, 2026-08-20),
non couverts ici :

- **C2** — types TypeScript générés depuis l'OpenAPI ;
- **C3** — alias Pydantic (couche d'absorption nom interne / nom publié) ;
- la décision « frontend = client public assumé ou surface interne séparée »
  (question 4 du dossier, non tranchée).
