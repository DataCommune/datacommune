# Capacité de démonstration & smoke production borné — runbook (Phase 46, Plan 02)

Instance : LXC 275 `datacommune-demo` @ <ip-ct-275> — API publique `https://api.datacommune.fr` (couverture 44/85).
Exigence : **OPS-01**. Décisions : **D-05** (budget), **D-06** (critères), **D-07** (charge locale / smoke supervisé).

La charge reproductible tourne **localement en ASGI** ; la production ne reçoit qu'un **smoke read-only borné et supervisé** (`autonomous: false`).

---

## 1) Budget réel de trafic (D-05)

Le budget n'est pas assumé, il est **mesuré** par le harness local à partir de la séquence Phase 45.

| Élément | Valeur | Source |
|--------|--------|--------|
| Modes de résolution certifiés | `adresse`, `gps`, `cadastre` | 45-VERIFICATION truths 1 & 14, GEO-02 |
| `calls_per_session` mesuré | **3** | une session de démo rejoue la triade une fois |
| Sessions publiques simultanées | 8 | D-05 |
| **Budget = 8 × 3** | **24 req/min/IP** | mesure locale (confirme la baseline provisoire 24) |
| Plafond public par IP | 120 req/min (C36, 2026-10-10 ; 30 avant) | `app/config.py` (`rate_limit_per_ip`), appliqué par `app/rate_limit.py` |
| Marge sous plafond | 96 req/min | 24 < 120 |

**Résultat mesuré (local, 8 sessions) :** `verdict=PASS`, `status_counts={"200": 24}`, `p50 ≈ 0,4 ms`, `p95 ≈ 0,6 ms` (ASGI en process, sans réseau ni DB), `quota_remaining` positif, `cache_misses=3` / `cache_hits=21` (le cache n'est observé que là où il existe : la 1re session peuple les 3 modes, les 7 suivantes tapent le cache). La valeur p95 en production sera mesurée par le smoke §3 (réseau + DB réels).

Le budget 24 reste **inchangé** : la mesure ne diffère pas de la baseline provisoire, donc aucune substitution nécessaire.

---

## 2) Harness local reproductible (D-06/D-07)

Fichier : `api/backend/scripts/verify_demo_capacity.py` — HTTPX `ASGITransport` autour du **vrai** `RateLimitMiddleware`, sur fixtures synthétiques (jamais de DB, jamais d'URL externe en `--local`).

```bash
# Depuis api/backend/ (venv verrouillé : --no-sync ; DEBUG=true requis par config.py)
DEBUG=true uv run --no-sync pytest tests/test_verify_demo_capacity.py -q
DEBUG=true uv run --no-sync python scripts/verify_demo_capacity.py --local --sessions 8
```

Critères vérifiés (D-06) :
- **Zéro 5xx** sous budget → sinon `verdict=FAIL` (`server_5xx`).
- **Quota mensuel non épuisé** → `quota_remaining > 0`, sinon `quota_exhausted`.
- **p50/p95 documentés** dans le rapport.
- **Cache observé uniquement là où il existe** (`cache_hits`/`cache_misses`).
- **429 déterministe + `Retry-After` entier positif au dépassement** — branche de dépassement couverte par la suite de tests (`test_middleware.py` D-06 + `test_verify_demo_capacity.py`).

Bornes refusées par le harness : `> 8` sessions locales, sessions non positives, toute URL non locale en `--local`.

Le rapport est **agrégé uniquement** (D-02) : `sessions`, `calls_total`, `status_counts`, `p50_ms`, `p95_ms`, `quota_limit/remaining`, `cache_hits/misses`, `verdict`, `failures`. Aucune adresse, query string, clé API ou payload brut.

---

## 3) Smoke production borné et supervisé (D-07) — checkpoint 46-02-03

> **`autonomous: false`.** Ce smoke n'est **jamais** lancé par la CI ni par le harness local. Il est exécuté **une seule fois, à la main, sous supervision, après déploiement**. Il refuse de tourner sous pytest.

Bornes strictes :
- **Maximum 2 sessions / 30 s**, profil **read-only** uniquement.
- Hôte cible **allowlisté** : `api.datacommune.fr` uniquement.
- Confirmation interactive obligatoire (token `smoke`).
- **Arrêt immédiat** au premier 5xx, 429 inattendu, ou quota anormal.
- **Ne pas** lancer de profil stress/soak (hors périmètre v3.5 — cf. v3.7).

Séquence opérateur :

1. Confirmer que le déploiement est vert et l'API saine :
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://api.datacommune.fr/v1/health
   ```
   Attendu : `200` (⚠️ le health public est `/v1/health` — `/health` nu = 404, gotcha 45-05).
2. Lancer le smoke supervisé, 2 sessions max, sur l'hôte allowlisté, et **taper `smoke`** à l'invite. Deux variables d'environnement sont **obligatoires** (fail-closed si absentes) :
   - `DC_SMOKE_API_KEY` : une **clé dédiée au smoke** (jamais la clé serveur `dc_live_`, jamais une clé de prod partagée) — elle n'est ni loguée ni incluse dans le rapport.
   - `DC_SMOKE_QUERY` : une **requête publique non-sensible** (ex. un code INSEE 44/85 comme `44109`) — jamais une adresse réelle ; elle n'apparaît pas dans le rapport (D-02).
   ```bash
   DC_SMOKE_API_KEY='<clé dédiée smoke>' DC_SMOKE_QUERY='44109' \
   DEBUG=true uv run --no-sync python scripts/verify_demo_capacity.py \
     --production-smoke --sessions 2 --url https://api.datacommune.fr
   ```
   Le probe envoie au plus `2 × 3` requêtes `GET /v1/resolve` bornées à 30 s, s'arrête au premier 5xx, et imprime un rapport JSON agrégé (statuts, p50/p95, verdict) — aucune adresse/coordonnée/clé.
3. Vérifier le rapport : bornes 2/30 respectées, **zéro 5xx**, quota nominal.
4. Consigner une **preuve sanitisée** (SHA candidat, environnement, timestamp UTC, agrégats seulement — jamais d'adresse/coordonnée/clé).
5. **Tout FAIL reste bloquant** : ne pas lever le checkpoint tant que le verdict n'est pas PASS dans les bornes.

Critères de sortie (checkpoint) :
- [ ] Bornes 2/30 respectées et zéro 5xx.
- [ ] Rapport sanitisé avec SHA, environnement, timestamp et agrégats.
- [ ] Verdict PASS ; tout FAIL bloque la phase.

Signal de reprise : `` `smoke capacité validé` `` (ou un statut FAIL sanitisé).

---

## 4) Rappels de conception

- Rate-limit **in-memory mono-instance** : on certifie le cadre homelab actuel, pas une architecture distribuée (pas de Redis, pas de cache global `/v1/resolve` sans preuve de besoin — D-07).
- `/v1/health*` est **hors rate-limit** : un test de charge sur ce chemin serait vacueux. Le smoke doit viser un endpoint public réellement protégé.
- Le nom du conteneur `datacommune-api-api-1` est figé sur le LXC 275 ; ne pas inventer un second projet Compose.
