# Monitoring externe & quota anonyme — runbook (Phase 37, Plan 07)

Instance : LXC 275 `datacommune-demo` @ <ip-ct-275> — API publique `https://api.datacommune.fr`.

## 1) Sondes externes (D-09) — UptimeRobot

Deux sondes distinctes, hébergées HORS homelab (une panne de l'hyperviseur ou du réseau doit être elle-même détectable) :

| # | Nom | Type | URL | Condition | Sévérité |
|---|-----|------|-----|-----------|----------|
| 1 | DataCommune — LIVE (critical) | HTTP | `https://api.datacommune.fr/v1/health/live` | non-2xx / timeout | critique |
| 2 | DataCommune — DATA HEALTH (dégradation) | Keyword | `https://api.datacommune.fr/v1/health` | keyword exists : `"statut":"degradee"` | warning |

Notes de conception :
- `/v1/health` renvoie **HTTP 503 quand dégradé** (RISK-1) — vérifié live le 2026-07-02 22:20 UTC (503 + `"statut":"degradee"`).
- Keyword `"statut":"degradee"` (double-e) : les entrées par dataset utilisent `degrade` (simple e), pas de faux match.
- **A1 RÉSOLU (empirique, 2026-07-02 22:22 UTC)** : sonde keyword créée pendant que l'endpoint était 503+degradee →
  après le 1er cycle, monitor **DOWN avec `cause: 503`** (incident 331197893205992631), PAS "keyword found".
  **UptimeRobot court-circuite l'évaluation keyword sur un statut non-2xx** : le split warning(keyword)/critical(down)
  est perdu tant que /v1/health est en 503, mais **l'alerte part quand même** — fallback acceptable prévu par
  37-RESEARCH/T-37-12. Tous les chemins alertent : 200+"degradee" → DOWN (keyword) ; 503 → DOWN (503) ; sain → UP.
- Alert contact : adresse personnelle (hors dépôt) (id 8582684), attaché aux 2 monitors (threshold 0, recurrence 0).
- Fallback SaaS documenté : **Better Stack free tier** si UptimeRobot indisponible/inadapté.

### Monitors créés (2026-07-02 22:20 UTC, API v3)

| id | friendlyName | type | statut 1er cycle |
|----|--------------|------|------------------|
| 803429708 | DataCommune LIVE critical | HTTP `/v1/health/live` (2xx/3xx, timeout 30 s, interval 300 s) | **UP** |
| 803429710 | DataCommune DATA HEALTH degradation | KEYWORD `"statut":"degradee"` ALERT_EXISTS CaseSensitive sur `/v1/health` | **DOWN (cause 503)** — attendu, l'instance est réellement dégradée |

**Gotchas API** (compte créé 2026-07 = nouvelle plateforme) :
- L'API **v2 legacy est en lecture seule** sur ce plan (`newMonitor` → `access_denied: not allowed with your current plan`) → utiliser **v3** (`Authorization: Bearer`, JSON).
- v3 exige `timeout` (≤ 60) ; `keywordCaseType` = `CaseSensitive`/`CaseInsensitive` (pas SCREAMING_CASE) ; contacts = `[{"alertContactId":"...","threshold":0,"recurrence":0}]` (POST create ne les attache PAS par défaut — vérifier `assignedAlertContacts` après création).

## 2) Garde D-11 live (préalable obligatoire au D-10) — PROUVÉ 2026-07-02

Précondition : l'exemption de quota anon (D-10) n'est sûre que si le plafond per-IP 30/min (D-11,
commit da983ff, Plan 37-03) tourne réellement sur le LXC (rebuild Plan 37-05 du 2026-07-02).

**⚠️ Correction du protocole du plan** : l'exemple du plan sprayait `/v1/health/live` — chemin
**exclu du rate-limit** (`rate_limit.py:69-81` skip `/v1/health*`, `/docs`, `/admin`…). Un spray
santé ne peut JAMAIS produire de 429 ; le premier essai (35× 200) était un faux négatif de test,
pas une absence de déploiement. Test valide = chemin non exclu.

Preuve (2026-07-02 ~21:15 UTC, IP unique, clé garbage distincte par requête) :

```
for i in $(seq 1 35); do curl -s -o /dev/null -w '%{http_code} ' \
  -H "X-API-Key: garbage-$i" "https://api.datacommune.fr/v1/d11-guard-test?n=$i"; done
→ 404 ×23 puis 429 à la requête #24 (2 s écoulées)
```

**429 au ~30/min per-IP** (les requêtes same-IP précédentes dans la fenêtre 60 s expliquent le
déclenchement à #24 au lieu de #31) — très loin du plafond per-key 500/min : les clés garbage
n'achètent plus de bucket individuel. **D-11 est LIVE → GUARD PASSED.**

## 3) Exemption quota anon (D-10) — APPLIQUÉ 2026-07-02

**⚠️ Écart vs plan** : le nom présumé `anonymous-bundle-key` (script `rotate_anon_key.py`)
n'existe PAS en prod (premier UPDATE → 0 ligne, sans effet). Clé anon réelle :
id=2, name=`frontend public anon key`, prefix `dc_anon_LbOh4Ghg…`, user_id NULL.
Scope retenu : `key_prefix LIKE 'dc_anon_%' AND user_id IS NULL` (1 ligne exacte, plus robuste
que le libellé d'affichage).

```sql
-- BEFORE : id=2, quota_monthly = 500
BEGIN;
UPDATE api_keys SET quota_monthly = 1000000
WHERE key_prefix LIKE 'dc_anon_%' AND user_id IS NULL;   -- UPDATE 1
COMMIT;
-- AFTER : id=2, quota_monthly = 1000000
```

- Valeur choisie : **1 000 000/mois** — haute mais finie ; le vrai garde-fou est le 30/min per-IP
  (≈ 1,3 M req/mois max pour UNE IP saturée en continu — la valeur ne mord jamais en usage légitime).
- Vérification post-commit : `GET /v1/commune?code_insee=44109` avec la clé anon → **HTTP 200**.
- **Rollback** : `UPDATE api_keys SET quota_monthly = 500 WHERE key_prefix LIKE 'dc_anon_%' AND user_id IS NULL;`
- `.env` / volume pgdata non touchés (op DB pure via psql stdin, aucune action compose).

## 5) Sonde golden synthétique (HEALTH-02, Plan 46-01) — activation host 46-01-03

Sonde privée quotidienne qui rejoue un `case_id` **opaque** du corpus golden
(Phase 43) contre le contrat final `/v1/resolve` et vérifie le bloc critique
**D-03** : identité de résolution valide, `code_insee` attendu, `id_parcelle`
attendu, présence du bloc `cadastre`, et présence `dvf` **uniquement** pour un
cas `with_dvf`. Fail-closed : timeout, 4xx/5xx, invariant faux, fixture invalide
ou livraison Apprise en échec → **exit non-zero** → l'unité `systemd` échoue.

- **Fixture hors Git (D-01)** : les entrées exactes vivent **uniquement** sur
  l'hôte, jamais dans le dépôt. Montée read-only dans le conteneur API via
  `api/docker-compose.vps.yml` (`${SYNTHETIC_PROBE_DIR:-/opt/datacommune/private/synthetic-probe}` → `/run/secrets/datacommune-probe:ro`).
- **Sortie allowlistée (D-02)** : logs et alertes ne contiennent que `case_id`,
  durée, booléens d'invariants et codes d'erreur normalisés — jamais l'adresse,
  la query, lat/lon, la clé ou la réponse brute.
- **Ne JAMAIS afficher** le contenu de `cases.json` (`cat`, `journalctl` de son
  contenu, copier-coller). Toute preuve est **sanitisée** (SHA + timestamp).

### 5.1 Installation de la fixture (permissions strictes)

```bash
# Dossier privé host, propriété du user qui exécute docker (uid propriétaire),
# 0700 = lui seul peut traverser ; fichier 0400 = lecture seule propriétaire.
sudo install -d -m 0700 /opt/datacommune/private/synthetic-probe
# Déposer cases.json (contenu jamais affiché ici) puis :
sudo install -m 0400 /chemin/hors-git/cases.json \
  /opt/datacommune/private/synthetic-probe/cases.json

# Vérifier permissions SANS afficher le contenu :
stat -c '%a %U %n' /opt/datacommune/private/synthetic-probe \
  /opt/datacommune/private/synthetic-probe/cases.json
# Attendu : 700 <owner> …/synthetic-probe   et   400 <owner> …/cases.json
```

Poser sur l'hôte (`.env` du stack, jamais en Git) la clé API de sonde et,
si besoin, le dossier / l'URL :

```
SYNTHETIC_PROBE_API_KEY=<clé API dédiée sonde active, quota normal>
# facultatif : SYNTHETIC_PROBE_DIR=/opt/datacommune/private/synthetic-probe
# facultatif : SYNTHETIC_PROBE_BASE_URL=https://api.datacommune.fr
```

La clé dédiée n'est pas une exemption de quota et le modèle actuel ne porte pas
de restriction par endpoint : la sonde utilise `/v1/resolve`, mais cela ne crée
pas un scope d'autorisation propre à cette route. Ne pas utiliser de clé admin,
de quota illimité ou de clé publique partagée pour cette tâche.

#### Provisionner ou remplacer une clé révoquée

Après déploiement du script versionné, utiliser
`scripts.provision_synthetic_probe_key` depuis l'image qualifiée. L'opérateur
prépare une nouvelle clé `dc_live_` aléatoire dans le gestionnaire de secrets et
la fournit par stdin depuis un fichier privé, jamais comme argument de processus,
dans Git ou en sortie terminal. Le script ne génère ni n'affiche de secret.

```sh
docker exec -i datacommune-api-api-1 python -m scripts.provision_synthetic_probe_key < /chemin/prive/nouvelle-cle-sonde
docker exec -i datacommune-api-api-1 python -m scripts.provision_synthetic_probe_key --verify-only < /chemin/prive/nouvelle-cle-sonde
```

La transaction crée une clé dédiée sans utilisateur, sans privilège admin et
avec le quota normal de 500/mois. Le succès n'est imprimé qu'après commit effectif.
Une répétition avec la même clé qualifiée ne modifie pas sa ligne ; une collision,
une ancienne clé révoquée, une clé legacy ou un marqueur/privilège incompatible
est refusé. `--verify-only` utilise une transaction SQL en lecture seule.

Si une ancienne clé **dédiée et encore active** doit rester disponible pendant
la bascule, renseigner explicitement `--previous-key-id ID_ATTENDU` lors du
provisionnement. Cela autorise une seconde clé nominale sans révoquer l'ancienne.
Après qualification du nouveau déploiement, retirer l'ancienne par le circuit
normal de révocation. Une clé révoquée ne doit jamais être réactivée pour réparer
la sonde. L'ID ne transporte aucune valeur secrète.

Installer la nouvelle valeur dans `SYNTHETIC_PROBE_API_KEY` du fichier `.env`
privé utilisé par CD, puis activer la configuration par le circuit PR/CD.
Une recréation seule ne corrige pas un `.env` qui contient encore la clé révoquée.
Vérifier la correspondance de configuration sans afficher la valeur ; après CD,
qualifier un parcours nominal borné avant de conclure que la sonde est rétablie.
Ce script ne modifie ni `.env`, ni timer, ni fixture, ni les gardes API.

Preuve reproductible d'atomicité, commit différé en échec, refus de collision et
rotation sans révocation dans un PostgreSQL jetable :

```powershell
pwsh -NoProfile -File scripts/run_contract_integration.ps1 tests/integration/test_synthetic_probe_key_cli.py -q --disable-warnings
```

Activer le montage RO et les variables par le circuit PR/CD, puis vérifier
le conteneur recréé par CD :

```bash
# Confirmer que le montage est bien read-only :
docker inspect datacommune-api-api-1 \
  --format '{{range .Mounts}}{{.Destination}} rw={{.RW}}{{"\n"}}{{end}}' \
  | grep datacommune-probe        # attendu : /run/secrets/datacommune-probe rw=false
```

### 5.2 Test nominal (doit sortir 0)

```bash
docker exec datacommune-api-api-1 python -m app.scheduler.run_job synthetic-probe
echo "exit=$?"     # attendu : 0, aucune valeur privée dans la sortie
```

### 5.3 Test négatif d'alerte (doit sortir ≠ 0 ET émettre une alerte Apprise)

But : prouver que le canal d'alerte fonctionne réellement, **sans** casser le
service. On pointe la sonde vers une fixture de contrôle volontairement fausse
(ex. `id_parcelle` attendu inexistant) — toujours hors Git, permissions 0400 —
puis on la relance :

```bash
sudo install -m 0400 /chemin/hors-git/cases-negatif.json \
  /opt/datacommune/private/synthetic-probe/cases.json
docker exec datacommune-api-api-1 python -m app.scheduler.run_job synthetic-probe
echo "exit=$?"     # attendu : 1 (fail-closed)
```

Vérifier la réception de l'alerte Apprise (canal `ALERT_URLS`) : titre
`Synthetic golden probe FAILED`, corps = `case_id` opaque + code d'erreur +
durée uniquement. **Restaurer immédiatement la vraie fixture** (0400) puis
re-run nominal → exit 0 avant d'activer le timer.

### 5.4 Activation du timer (après nominal PASS + alerte reçue)

```bash
sudo cp infra/systemd/datacommune-job@.service /etc/systemd/system/
sudo cp infra/systemd/datacommune-job@synthetic-probe.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now datacommune-job@synthetic-probe.timer
systemctl list-timers 'datacommune-job@synthetic-probe*'   # prochaine échéance
```

### 5.5 Diagnostic

```bash
# Dernier run (logs = D-02 uniquement, aucun secret)
journalctl -u 'datacommune-job@synthetic-probe.service' -n 50 --no-pager
# Un run rate rend l'unité "failed" :
systemctl status datacommune-job@synthetic-probe.service
```

Codes d'erreur normalisés : `timeout`, `http_<code>`, `identity_absent`,
`resolution_not_exact`, `code_insee_mismatch`, `id_parcelle_mismatch`,
`cadastre_absent`, `dvf_absent`, plus les erreurs de fixture (`fixture …`).

### 5.6 Rollback (désactivation propre, aucune donnée touchée)

```bash
sudo systemctl disable --now datacommune-job@synthetic-probe.timer
sudo rm -f /etc/systemd/system/datacommune-job@synthetic-probe.timer
sudo systemctl daemon-reload
# Optionnel : retirer la fixture host (op fichier pure, pgdata/.env non touchés)
sudo rm -f /opt/datacommune/private/synthetic-probe/cases.json
```

La sonde étant hors du chemin requête public, sa désactivation n'a **aucun**
impact sur l'API ni sur les données.

## 6) Observations hors scope (à traiter séparément)

- `api_keys` id=8 « Codex High Quota Test Key » (user_id NULL, quota 2 147 483 647, active) —
  clé de test résiduelle en prod, candidate à révocation.
- Chemins de test : `/v1/commune` prend `code_insee` (pas `code`) ; pas d'endpoint public `/v1/auth/keys/quota`.
