# Getting Started

DataCommune est une API REST qui expose 14 datasets publics francais via un point d'entree unique. Ce guide vous amene de zero a votre premiere reponse API en moins de 5 minutes.

## Prerequisites

- Docker + Docker Compose installes
- DataCommune en cours d'execution (localement ou instance hebergee)

## Step 1 : Demarrer DataCommune

> Si l'instance tourne deja, passez a l'etape 2.

```bash
cp .env.example .env  # configurer POSTGRES_PASSWORD
docker compose up -d
docker exec datacommune-api-1 alembic upgrade head
```

Pour les details complets de l'installation, voir [docs/CONTEXT.md](CONTEXT.md).

## Step 2 : Creer une cle API

Tous les endpoints (sauf `/v1/health`) exigent un header `X-API-Key`.

```bash
docker exec datacommune-api-1 python -m app.auth.cli create-key "mon-app"
```

Sortie :

```
Cle API creee avec succes (id=1)
Nom: mon-app
Quota mensuel: 10000
Cle: aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789-_abc

ATTENTION: Cette cle ne sera plus affichee. Conservez-la en lieu sur.
```

Conservez cette cle. Elle ne sera plus jamais affichee.

## Step 3 : Premier appel -- Geocodage

### curl

```bash
curl -H "X-API-Key: VOTRE_CLE" \
  "http://localhost:8000/v1/geocode?q=1+rue+de+la+Paix+Paris"
```

### Python

```python
import httpx

response = httpx.get(
    "http://localhost:8000/v1/geocode",
    params={"q": "1 rue de la Paix Paris"},
    headers={"X-API-Key": "VOTRE_CLE"},
)
data = response.json()
print(data["donnees"][0]["latitude"])  # 48.8684
```

### JavaScript

```javascript
const response = await fetch(
  "http://localhost:8000/v1/geocode?q=1+rue+de+la+Paix+Paris",
  { headers: { "X-API-Key": "VOTRE_CLE" } }
);
const data = await response.json();
console.log(data.donnees[0].latitude);  // 48.8684
```

## Step 4 : Decoder la reponse

```json
{
  "statut": "ok",
  "donnees": [
    {
      "ban_id": "75102_7040_00001",
      "numero": "1",
      "nom_voie": "Rue de la Paix",
      "code_postal": "75002",
      "nom_commune": "Paris 2e Arrondissement",
      "longitude": 2.3312,
      "latitude": 48.8684,
      "score": 0.95
    }
  ],
  "meta": null
}
```

| Champ | Description |
|-------|-------------|
| `statut` | `"ok"` en cas de succes, `"erreur"` sinon |
| `donnees` | Les resultats (objet ou liste selon l'endpoint) |
| `meta` | Metadonnees optionnelles (source, licence) |

Les endpoints pagines (DVF, DPE, batiments, monuments) utilisent `ReponsePaginee` avec un champ `pagination` au lieu de `meta`.

## Step 5 : Aller plus loin

### Resolution multi-dataset

L'endpoint `/v1/resolve` detecte automatiquement le type d'entree (adresse, GPS, cadastre) et interroge plusieurs datasets en une seule requete :

```bash
curl -H "X-API-Key: VOTRE_CLE" \
  "http://localhost:8000/v1/resolve?q=48.856,2.352&datasets=dvf,dpe,loyers"
```

### Endpoint pagine

Les endpoints comme `/v1/dvf` retournent des resultats pagines par curseur :

```bash
# Premiere page
curl -H "X-API-Key: VOTRE_CLE" \
  "http://localhost:8000/v1/dvf?lat=48.86&lon=2.34&rayon_m=500&limite=20"
```

Si `curseur_suivant` est present dans `pagination`, utilisez-le pour la page suivante :

```bash
curl -H "X-API-Key: VOTRE_CLE" \
  "http://localhost:8000/v1/dvf?lat=48.86&lon=2.34&rayon_m=500&limite=20&curseur=eyJvZmZzZXQiOjIwfQ"
```

## Codes d'erreur

| Code | Signification |
|------|---------------|
| 401 | Header `X-API-Key` manquant |
| 403 | Cle invalide ou revoquee |
| 422 | Parametres invalides |
| 429 | Quota mensuel depasse (defaut : 500 req/mois, plan Decouverte) |

Les erreurs suivent le format RFC 9457 Problem Details (`type`, `title`, `status`, `detail`).

## Next Steps

- **Swagger UI** : http://localhost:8000/docs -- documentation interactive, testez les endpoints directement
- **ReDoc** : http://localhost:8000/redoc -- reference API complete
- **Licences** : [LICENSES.md](../LICENSES.md) -- licences et obligations de chaque dataset
