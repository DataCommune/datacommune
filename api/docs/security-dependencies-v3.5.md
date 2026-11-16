# Audit des dépendances v3.5 (SEC-01 — D-08/D-09)

**Date de l'audit :** 2026-07-22
**Portée :** lockfiles réels `api/backend/uv.lock` (Python) et `frontend/package-lock.json` (npm).
**Outils :** `pip-audit` (via `uv export --locked` → requirements → `pip-audit --format json`), `npm audit --json`.
**Verdict automatisé :** PASS fail-closed (`scripts/validate_dependency_audit.py`).
**Sign-off reachability humain :** checkpoint **46-03-03** (non encore signé — voir « Statut »).

> Ce document ne contient que des identifiants d'advisory, des noms de paquets,
> des sévérités et des scopes. Aucune valeur de secret, aucune chaîne détectée.

## Méthode

Le lock est la source autoritative ; les dépendances ne sont jamais ré-résolues
librement depuis les manifests.

- **Python** : `uv export --locked --no-emit-project` (vue complète) et
  `--no-dev` (vue runtime), puis `pip-audit -r <requirements> --format json`.
- **npm** : `npm audit --omit=dev --audit-level=high --json` (vue runtime) et
  `npm audit --json` (arbre complet build/dev). `--audit-level` ne filtre pas le
  JSON : le validateur lit toutes les sévérités.
- Aucune commande `fix`/`--force` n'est exécutée en audit : elle muterait la
  résolution. Les upgrades restent des tâches atomiques (diff lock + tests +
  rollback).

## Résultats

| Écosystème | Vue runtime (`--omit=dev` / `--no-dev`) | Arbre complet (build/dev) |
|---|---|---|
| Python (pip-audit) | 0 finding | 0 finding |
| npm | 0 finding | 3 low + 3 high (6 total, 2 advisories réels) |

Aucune vulnérabilité **runtime** critique/haute : la barre D-08 est tenue sans
remédiation runtime. Toutes les alertes restantes sont **dev/build-only** et
tracées ci-dessous.

## Findings triés (arbre build/dev)

| Advisory | Paquet | Sévérité | Scope | Chemin | Décision | Expire |
|---|---|---|---|---|---|---|
| GHSA-f88m-g3jw-g9cj | sharp `<0.35.0` | high | dev | wrangler → miniflare → sharp | accept-temporary | 2026-08-21 |
| GHSA-pxg6-pf52-xh8x | cookie `<0.7.0` | low | dev | @sveltejs/adapter-cloudflare → @sveltejs/kit → cookie | accept-temporary | 2026-08-21 |

Les 3 nœuds « high » remontés par `npm audit` (`sharp`, `miniflare`, `wrangler`)
et les 3 nœuds « low » (`cookie`, `@sveltejs/kit`, `@sveltejs/adapter-cloudflare`)
se réduisent aux **2 advisories réels** ci-dessus ; les autres lignes sont des
nœuds de propagation (`via` = référence, pas advisory).

### Reachability (à confirmer au checkpoint 46-03-03)

- **sharp (GHSA-f88m-g3jw-g9cj)** — dépendance optionnelle de `miniflare`
  (serveur de dev local Cloudflare Workers) tirée via `wrangler`. Traitement
  d'image du harnais de dev/preview uniquement ; jamais embarqué dans le bundle
  Workers déployé. Le fix (`sharp>=0.35.0`) n'est atteignable que par
  `npm audit fix --force` (bump/downgrade majeur de `wrangler`) → différé en
  tâche d'upgrade atomique.
- **cookie (GHSA-pxg6-pf52-xh8x)** — transitive build-time de `@sveltejs/kit`.
  Sévérité basse ; aucun chemin runtime ne passe d'attributs de cookie non
  fiables. Différé en upgrade SvelteKit atomique.

Un `npm audit fix` (non-force) a été tenté puis **reverté** : il ne résout aucun
des 6 findings (le fix exige `--force`) et ne faisait que churner le lockfile.

## Exceptions machine-readable

Registre : `security/dependency-exceptions.json`. Chaque entrée porte
`advisory`, `package`, `scope`, `reachability`, `owner`, `justification`,
`opened_at`, `expires_at` (horizon ≤ 30 jours, jamais passé). Le validateur
`scripts/validate_dependency_audit.py` refuse tout finding sans exception valide
et toute exception incomplète, expirée ou > 30 jours.

## Statut

- [x] pip-audit Python : 0 finding (runtime + full).
- [x] npm runtime (`--omit=dev`) : 0 finding.
- [x] npm full : 2 advisories dev/build, exceptions rédigées, verdict validateur PASS.
- [ ] **46-03-03** : confirmation humaine de la reachability + bascule
  `reviewed_by_human: true` liée au SHA candidat (bloquant pour CERT-01).
