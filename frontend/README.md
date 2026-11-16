# DataCommune Frontend

SPA SvelteKit 5 — expérience **Fiche** (Commune / Bien), hub « Mon espace », admin, et site marketing/landing.

> Chat IA et explorateur générique **supprimés** du projet v3.0.
> `frontend/landing/` reste conservé tant qu'aucun tag Git de préservation n'est vérifiable.
> Paradigme **TABLE-FIRST** : jamais de carte MapLibre/WebGL.

Production : [datacommune.fr](https://datacommune.fr) | Dev : [dev.datacommune.fr](https://dev.datacommune.fr)

## Fonctionnalités

- **Fiche Commune** (`/commune/[code]`) — KPI (L0) + thèmes L1/L2, mapper honnête API→Fiche (dégrade proprement quand une donnée manque)
- **Prospection** — recherche par critères, états gérés (idle/loading/error/empty/content)
- **Mon espace** — hub authentifié (historique/favoris)
- **Marketing/Landing** — homepage, /datasets, /guides, /reference, /playground (bac à sable API), /roadmap, /docs, pages légales
- **Auth** — login, register, forgot/reset password, verify email
- **Billing** — intégration Stripe (checkout, portal) — désactivée sur l'instance démo publique
- **Admin** — panel d'administration TABLE-FIRST (ETL, users, subscriptions, api-keys, datasets, system)

## Stack

| Composant | Technologie |
|-----------|-------------|
| Framework | SvelteKit 5 (runes mode), adapter-node |
| Svelte | 5.51+ ($state, $derived) |
| Build | Vite 7, TypeScript 5.9 strict |
| CSS | Tailwind v4 (@theme dans app.css) |
| Tables | Tables HTML sémantiques (TABLE-FIRST) |
| Cartes / Stats | Aucune (Leaflet + d3-array retirés en v3) |
| Markdown | mdsvex pour pages guide (.svx) |
| Tests | Vitest (unit, jsdom), Playwright (e2e) |
| Deploy | Aucun : `npm run build` produit `build/`, exécutable par `node build`. Cible d'hébergement décidée en phase 5 |

## Démarrage rapide

```bash
npm install
npm run dev        # proxy vers l'API locale sur 127.0.0.1:8000
npm run build
npm run preview    # serveur de prévisualisation Vite
npm run check      # svelte-check (types)
```

## Structure

```
src/
├── routes/
│   ├── (marketing)/      # Homepage, /datasets, /guides, /roadmap, /docs, /playground, légales
│   ├── (auth)/            # /login, /register, /forgot-password, /reset-password, /verify
│   ├── (app)/             # /mon-espace, /commune/[code] (Fiche), /admin, /settings, /billing
│   ├── +layout.svelte     # Root layout (imports app.css)
│   ├── +layout.ts         # SSR par défaut ; le groupe (app) active le mode SPA
│   └── +error.svelte      # 404 SPA propre
│
├── lib/
│   ├── api/               # client.ts (fetch + X-API-Key), auth, commune, billing, admin
│   ├── components/        # CommuneSearch, compare/, fiche/ (KpiStrip), admin/, marketing/ (Hero, Pricing, InteractiveDemo…)
│   ├── stores/            # Svelte 5 runes ($state classes) — auth.svelte.ts
│   ├── data/              # commune-fiche.ts, dataset-slugs.ts (redirections des anciennes pages /datasets/{slug})
│   ├── types/             # fiche.ts (KpiItem, KvRow, ThemeModel, Verdict)
│   ├── utils/             # csv-download.ts, export.ts
│   └── config.ts          # URLs centralisées (SITE_URL, API_URL, BAN_API_URL)
│
├── app.html               # lang="fr", font Inter
├── app.css                 # Design tokens of the UI contract, @theme Tailwind
└── app.d.ts                # Types App.Platform (adapter-node n'en fournit pas)
```

## Design System

Jetons du contrat de conception de la phase 3 (suivi interne) dans le
`@theme` de `src/app.css` ; police Inter seule, auto-hébergée (woff2). Garde : `npm run qa:design`.
Pas de dark mode, pas de CDN externe (privacy-first).

## Build et exécution

```bash
npm run build        # build strict (0 avertissement toléré) → build/, puis retrait des .map
node build           # sert l'application (adapter-node, PORT/HOST par variables d'environnement)
npm run release:guard # clé publique validée + build + scan du bundle public
```

**Aucun déploiement n'est configuré dans ce dépôt.** L'adaptateur Worker Cloudflare et
wrangler ont été retirés au plan 01-06 ; la cible d'hébergement (LXC/systemd ou Docker)
est décidée en phase 5. Les en-têtes de sécurité des réponses statiques et prérendues
devront être fournis par cette couche d'hébergement (KI-P3) : `src/hooks.server.ts` ne
couvre que le rendu à la demande.

## Conventions

- **TABLE-FIRST** : jamais MapLibre/WebGL ni carte principale
- **Marbre & Bronze** : tokens CSS (`bg-marble`, `text-bronze`), pas de `bg-white`
- **Accents français** : obligatoires dans toutes les strings UI
- **Stores runes** : classes $state/$derived, pas de writable/readable legacy
- **Pas de shadcn-svelte** : composants custom ou Bits UI
- Commits : `feat|fix|refactor|docs|test|chore(scope): msg`
- Code/commentaires en anglais, UI en français

## Env

| Variable | Prod | Dev |
|----------|------|-----|
| VITE_API_URL | URL fournie au workflow manuel | http://127.0.0.1:8000 |
| PUBLIC_DC_API_KEY | clé publique frontend | clé dev |

## Licence

Proprietary — All rights reserved.
