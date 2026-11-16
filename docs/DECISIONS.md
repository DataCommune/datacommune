# Décisions

Décisions **en vigueur** : décision, motif, date. Trois pages au plus (D-10) ; le raisonnement
complet vit dans le suivi interne du projet (cadrage, bilans de la phase 1) et dans
`DataCommune-Analyse-Contexte/`.
Une décision amendée le dit et garde sa date.

## Décisions de cadrage

Source : suivi interne (Key Decisions).

| Décision | Motif | Date |
|---|---|---|
| GSD organise le projet | Demande explicite | 2026-09-19 |
| Six parcours : commune, adresse, bien, et la comparaison de chacun | Précision utilisateur | 2026-09-19 |
| Produit = site grand public, servi par la plateforme de données | La technique repose dessus | 2026-09-19 |
| Reprise **corrective**, pas reconstruction | 14 incidents déjà corrigés | 2026-09-19 |
| Base = `3394395a`, pas la version servie avant la stabilisation | Porte les correctifs, jamais déployée : gate locale obligatoire | 2026-09-19 |
| Dépôt neuf, code copié **sans historique** | Repartir propre ; provenance nommée ; gitleaks avant distant | 2026-09-19 |
| 27 datasets et les mêmes informations qu'avant en V1, sur 44/85 | Ne rien abandonner, tout ordonner | 2026-09-19 |
| Stack inchangée : Python/FastAPI, PostgreSQL/PostGIS, SvelteKit | Dépendances réexaminées une par une | 2026-09-19 |
| Instance hébergée par Ianis **et** auto-hébergement possible | Accessibilité, contributions | 2026-09-19 |
| Documentation recréée à jour | L'ancienne est périmée et surabondante | 2026-09-19 |
| Nettoyage d'abord, chantiers de fond ensuite | Ordre proposé par Ianis | 2026-09-19 |
| Cinq phases, transfert sélectif en phase 1, pas de cadrage séparé | On ne tranche pas sur un code qu'on n'a pas en main | 2026-09-19 |
| Garde-fous hérités : parité par transfert, plafond de doc, recette externe tôt, DPE documentaire avant identité, sauvegarde tôt, licences avant ouverture | Erreurs mesurées | 2026-09-19 |
| Le suivi interne sort du dépôt avant toute ouverture publique | Éviter 93 % de pilotage dans un dépôt public | 2026-09-19 |
| Exécution par agents, orchestrateur en vérification | Économiser le contexte principal | 2026-09-19 |

## Décisions de phase 1

Source : `01-CONTEXT.md`, toutes datées du **2026-09-19** ; amendements 2026-09-20 et 2026-09-21.

| # | Décision | Motif | État |
|---|---|---|---|
| D-01 | Copie brute de `3394395a`, pas de sélection à la copie | Ne pas trier avant de savoir ce qui sert | **Amendée** |
| D-02 | Les quatre suites passent **avant** tout retrait | Sans point vert connu, un retrait n'est pas mesurable | Tenue (`d87f6e3`) |
| D-03 | Retraits un par un : appelants + suppression + tests verts + un commit | Un lot mélangé rend la cause d'un rouge indécidable | **Amendée** : liste corrigée |
| D-04 | Comptes, clés API, quotas, administration restent ; si le découplage auth ↔ billing dépasse un lot borné, on rapporte | L'instance en dépend | Hors bornes, **rapporté** |
| D-05 | Un retrait est réversible : SHA d'origine et motif dans REPO-STATE | Revenir sans archéologie | Tenue |
| D-06 | Le type DPE (`exact/multiple/building` sans producteur) n'est pas réduit ici | Lot de phase 4 | Tenue |
| D-07 | Avant tout distant **public**, le suivi interne part vers un dépôt privé | Ne pas publier le pilotage | Sous réserve : distant privé, suivi interne poussé, **rien n'en rappelle la sortie** |
| D-08 | Provenance au README ; gitleaks avant le premier commit de code | Traçabilité, hygiène de secrets | Tenue |
| D-09 | Les 13 scripts d'audit et `docs/operations/` restent dans l'archive privée | Hors périmètre public | Tenue — garde perdue, KI-P4 |
| D-10 | Trois documents de trois pages au plus ; le détail renvoie à l'archive ou à l'analyse | L'ancien régime ne doit pas revenir | Tenue (≤ 180 l., ≤ 12 000 c.) |
| D-11 | Ratio documentation / code mesuré par commande versionnée, écrit dans REPO-STATE | Un plafond non mesuré n'en est pas un | Tenue (`doc-code-ratio.py`) |
| D-12 | Aucune action sur l'hyperviseur ; dump en phase 2 depuis la sauvegarde PBS de l'ancien conteneur de production | Ce conteneur a disparu ; sauvegardes du 14 au 18/09 | **Amendée** (D-16b) |
| D-13 | Machine de test = ce poste Windows, Docker sur `D:`, PostGIS épinglé, ressources arrêtées | 800 Go libres sur `D:` | **Amendée** : D-16b, `bind-mount` retiré (D-17) |
| D-14 | Runtime GSD = Claude Code, agents réinstallés | Sinon les sous-agents retombent en génériques | Tenue (64 agents, 0 manquant) |
| D-15 | Un lot actif, un propriétaire par fichier, relecture avant intégration par un relecteur indépendant | Les tempêtes de commits de septembre | **Amendée** : relecteur indépendant ; proportionnée par D-119 (2026-10-12) |

### D-03 amendée — liste de retraits fausse trois fois, 2026-09-20

Sur huit retraits « indicatifs », trois n'ont pas eu lieu comme listés : preuve bâtiment RNB
**sans objet** (retirée en amont le 2026-08-06 ; le nom porte la Règle 2 d'élargissement
bâtiment, vivante), `dpe_par_identifiant_ban` **refusé** (trois appelants hors `app/`, dont la
gate verte), dépendance `apscheduler` **gardée** (KI-05) ; Stripe **reporté** (option c, D-04).
Verdicts et commits : `REPO-STATE.md` § Retraits, `01-08-SUMMARY.md`.

**Règle qui en sort** : une liste de retraits est une hypothèse, pas une autorisation de
supprimer. La recherche d'appelants tranche sur **tout** le dépôt : bornée à `app/`, elle a
produit deux des trois erreurs ci-dessus.

### Autres amendements, 2026-09-20 et 2026-09-21

**D-01, deux dérogations** : six éléments d'instructions d'agents retirés à la copie (arbitrage
`remove` de Ianis) et `infra/vault.yml` (« secrets hors dépôt » prime) ; liste : `REPO-STATE.md`.
**D-13, dérogation `bind-mount`** : données PostgreSQL sur `D:`, jamais exercée, retirée par D-17.

**D-15 amendée — relecteur indépendant, 2026-09-21.** Conservés : la ligne D-15 et l'interdiction
de fusion automatique de worktree. Retirée : la qualification de Sol, Terra et Luna — contrainte
orpheline dont aucune phase ne dépend : la phase 2 n'est plus bloquée. Ajouté : relecteur distinct
de l'exécuteur, session ou modèle n'ayant pas produit le diff ; modèle libre, auto-revue exclue.

## Décisions de phase 2

D-16 à D-33 (2026-09-21, amendements du 2026-09-23) :
suivi interne (contexte de la phase 2). Amendent D-12 (actions sur l'hyperviseur
autorisées) et D-13 (le poste Windows garde les tests destructifs, plus les données) ; D-17
appliquée (`2a95b8a`, volume nommé).

## D-54 — Dérogations de phase 2, 2026-09-25

Liste : `02-BLOCAGES.json` (14 entrées `deroger`, 6 après 02-21 et 5 après 02-22, tous deux du 2026-09-26). Datées du 2026-09-25 :

- antennes, ban, copropriete, demographie, dpe, dvf, fiscalite, georisques, loyers, revenus,
  urbanisme : servis, écarts < 2 % ou golden adresse rouge faute de cadastre ; dérogation
  **approuvée** (orchestrateur, délégation de Ianis).
- cadastre, connectivite : non hébergés en V1 phase 2, décision de Ianis, **approuvée** ;
  réintégrés le 2026-09-26 (02-21, 02-22) ; plus aucune dérogation d'hébergement.
- monuments : réparé en 02-20, écart inexpliqué dérogé (D-28), **approuvée** (orchestrateur, délégation).

## Décisions de phase 3

D-34 à D-53 (2026-09-23) : suivi interne (contexte de la phase 3) ; retraits et
relectures : `03-*-SUMMARY.md`.

- **D-47, forme retenue** (Ianis, 2026-09-24, 03-06) : option a — champ additif `etats`
  `{etat, motif}` par colonne plate servie à `null` ; aucun champ publié ne change de type ;
  `suppressed` n'est pas produit en phase 3.
- **D-37 amendée — relecteur** (Ianis, 2026-09-24, « Opus 5.5 en priorité ») : la relecture
  D-15 passe de Fable à Opus 5.5, en contexte neuf, depuis 03-08.
- **R1 — référentiel national** (Ianis, 2026-09-26, « go A », 03-14) : `geo_communes` couvre la
  France (liste du producteur), les données restent sur 44/85 ; hors couverture ≠ inconnu. Amende 02-11.

## Décisions de phase 4

D-101 à D-118 (2026-09-30) : `04-CONTEXT.md` de la phase 4.

- **DPE-01** (Ianis, 2026-10-01, 04-01) : option-d — « DPE déclarés à cette adresse », liste
  paginée, plus récent (égalité dite), badges, aucun logement ; D-110 retenue (adresse stricte,
  un bâtiment hors annexes, point BAN), non consultables non servis. `04-MESURE-DPE.md`.
- **2026-10-02** (Ianis) : adresse déclarée douteuse masquée en entier (P-ADR-01) ; usage
  inconnu compté pour D-110 (P-D110-NULL) ; repère ③ « Argiles au point » ; parcelle du seul
  point BAN, jamais la plus proche ; P-PARCELLE ouverte. `04-CONTEXT.md`.
- **D-119 — rythme proportionné au risque** (Ianis, chat, 2026-10-12, « ok pour 1 à 5 ») :
  1. Phase 4 close sans nouveau lot de correction ; le non bloquant part en todos de 4.1.
  2. D-15 amendée : une relecture indépendante en fin de phase ; en cours de phase, relecture
     ciblée seulement pour sécurité, confidentialité, migration ou sens des données. Les
     corrections issues d'une relecture ne sont pas relues à leur tour, sauf critique (CR).
  3. Livraison par lot fonctionnel, pas par tâche rapide : tests ciblés à chaque commit ;
     matrice, déploiement, golden et rejeu réel une fois avant la livraison groupée.
  4. Suivi allégé : `STATE.md` et `HANDOFF.md` réduits à la position courante, l'historique
     archivé ; SUMMARY courts (changé, vérifié, reste).
  5. Textes délégués à l'agent dans les règles existantes (glossaire, quatre états, I-1 à I-8) ;
     Ianis les relit en bloc à la recette au lieu de les valider ligne à ligne avant le code.
  Inchangés : gates (matrice, golden, SEM-02), invariants, publication = geste de Ianis.

## Décisions de phase 4.2

- **D-130 — France entière sur l'hyperviseur** (Ianis, 2026-10-29, relayé par la session « ★ DC · Orchestrateur ») :
  plus de location OVH. La base de travail nationale est dans son conteneur ; le site public reste dans le conteneur public, avec le même
  tunnel et la même procédure. Le stockage est un pool ZFS zstd (recordsize 32K) posé sur un LV thin de 400G :
  l'occupation de DataCommune est plafonnée et ne peut pas remplir le pool thin partagé. Publication par clone
  ZFS si elle est prouvée sur 44/85, sinon par restauration dans une base parallèle. Seconde copie de l'archive sur
  le disque D: du poste (le VPS n'a que 38 Go libres). Remplace H-2 amendée (2026-09-30) ; mesures, pistes et
  budget dans le suivi interne de la phase 4.2. Inchangés : invariants I-1 à
  I-8, quatre états, recette de Ianis et deux testeurs extérieurs.

## Décisions de phase 5

Numéros globaux ; le numéro local de `05-CONTEXT.md` est entre parenthèses. Décisions d'Ianis du 2026-11-04 : réponse « Tout recommandé » au dossier de décisions de la vague 1 (suivi interne).

- **D-131 — D-130 amendée** (Ianis, 2026-11-02) : la production publique ira sur un serveur acheté fin novembre 2026 ;
  l'hyperviseur redevient le banc de dev (branche `dev`) ; d'ici la bascule, le conteneur public sert la France entière.
  Dimensionnement dans le suivi interne (estimation du serveur, 2026-11-03). Reprise de la ligne de `PROJECT.md` (Key Decisions).
- **D-132 — Panne de publication (D-11 de la phase)** : un échec avant l'échange laisse servie l'ancienne génération
  complète ; un état que l'API ne peut pas prouver cohérent répond 503 avec un motif et une durée, jamais un mélange
  de générations (I-7). C'est le comportement de `publication_readiness` et du protocole COR-20. Preuve sur la base
  nationale : 05-04, à venir.
- **D-133 — Ouverture sans comptes ni offre payante ([VI-3], D-14 de la phase)** (Ianis, 2026-11-04, option a) :
  `VITE_ACCOUNTS_ENABLED`, `EMAIL_ENABLED` et `STRIPE_ENABLED` restent à `false` ; quota anonyme de 100 fiches
  distinctes par 7 jours (`FICHE_QUOTA_WEEKLY`, décidé le 2026-10-24) ; un professionnel qui dépasse reçoit une clé
  d'API créée à la main depuis l'administration ; aucune page ne propose d'abonnement ; le code `app/billing/` reste.
  Seuils gratuit et payant fixés après quatre à six semaines d'usage mesuré (GoatCounter, `quota_usage`).
  **Amendée le 2026-11-12 par Ianis** (« créer des cookies, ajouter des comptes, sur le compte on met une clé
  API, avec des limites ») : des **comptes gratuits** donnent une clé d'API personnelle aux limites du plan
  Découverte (500 requêtes par mois, 10 par minute, 2 clés) ; courriel de vérification par SMTP Migadu ;
  l'API reste ouverte **sans clé**, par IP (120/min, 2 000/jour). Toujours **aucune offre payante** :
  `STRIPE_ENABLED` et `VITE_BILLING_ENABLED` restent à `false`. Ouverture par configuration
  (`REGISTRATION_ENABLED`, `EMAIL_ENABLED`, `KEYLESS_ACCESS_ENABLED`, `VITE_ACCOUNTS_ENABLED`), fermée par
  défaut, déployée avec l'accord d'Ianis. Détail : suivi interne (réutilisation data.gouv, 2026-11-12).
- **D-134 — Seuils d'acceptation PERF-01 ([VI-4], D-16 de la phase)** : voir ci-dessous.

Décisions d'Ianis du 2026-11-10 sur l'ouverture du code (cadrage du dépôt public, § 8 ; elles remplacent les
recommandations provisoires de [VI-1] et [VI-2]) :

- **D-135 — Deux dépôts, le public porte la vérité du code ([VI-2], stratégie « B2 »)** : un dépôt public neuf, sans
  historique, dont `main` est la vérité du code (rien n'est servi en production qui n'y soit pas) ; le dépôt de
  travail reste privé et en est un sur-ensemble (pilotage, valeurs de machines). Règle vérifiable par commande :
  arbre du privé moins les chemins privés = arbre de `main` public. Remplace la recommandation « vitrine synchronisée
  par export » du 2026-10-31. Réversibilité : réversible tant que rien n'est poussé, coûteuse ensuite (un contenu
  publié reste dans les miroirs).
- **D-136 — Licence `AGPL-3.0-or-later` et DCO ([VI-1])** : identifiant SPDX `AGPL-3.0-or-later` partout (`LICENSE`,
  `pyproject.toml`, `package.json`, message du commit d'export) ; contributions sous Developer Certificate of
  Origin 1.1 (ligne `Signed-off-by`), pas de CLA. Réversibilité : sens unique dès la première contribution extérieure
  acceptée (relicencier exige alors l'accord de chaque contributeur).
- **D-137 — Dépôt public `DataCommune/datacommune`** : dans l'organisation GitHub `DataCommune`, qu'Ianis déclare
  sienne (propriété affirmée par lui, non vérifiable par l'API). Fixe l'URL du lien « Code source ». Réversible
  avant le premier push.
- **D-138 — Auteur des commits publics** : le compte GitHub d'Ianis avec son adresse `noreply` de GitHub. L'export
  lit l'auteur dans `DC_EXPORT_AUTHOR_NAME` et `DC_EXPORT_AUTHOR_EMAIL` ; aucune valeur personnelle n'est écrite
  dans le dépôt. Remplace l'auteur `DataCommune <contact@datacommune.fr>` recommandé le 2026-10-31. Réversible
  avant le premier push.
- **D-139 — Ouverture avant la migration de fin novembre** : le dépôt public ouvre dès que les lots 0 à 7 de 05-07
  sont verts ; si ce n'est pas le cas à temps, la migration ne les attend pas et l'ouverture passe après. Remplace
  « publié deux semaines après une bascule sans incident » (2026-10-31) et lève la dépendance de 05-07 à 05-06.
- **D-140 — scripts de l'hyperviseur et valeurs de machines côté privé** : les scripts de l'hyperviseur et leurs
  réglages, `infra/public/public.env` et les inventaires Ansible ne sortent pas dans le dépôt public ; les scripts publics lisent
  leurs valeurs dans un fichier `*.local.env` ignoré. Remplace « garder les noms d'hôte et numéros de CT » du
  2026-10-31. Mise en œuvre : lot 4 de 05-07.
- **D-141 — Langue des textes publics** : README et fichiers de communauté en français, avec un résumé anglais en
  tête du README ; code, identifiants, commentaires et commits en anglais.
- **D-142 — Branche par défaut du dépôt public : `master`** (Ianis, 2026-11-11) : même nom que dans le dépôt de
  travail. Les workflows (`push: branches: [master]`) et le cliquet de dette de design restent identiques des deux
  côtés ; partout où D-135 et le cadrage écrivent `main`, lire `master`. Conséquence : le premier commit public n'a
  pas de parent, et le cliquet prend alors ce commit pour référence (origine `racine`) au lieu d'échouer. Réversible
  avant le premier push.

### Seuils d'acceptation PERF-01

Décidés par Ianis le 2026-11-04, avant toute mesure sur le serveur acheté (D-16).

| ID | Mesure (sur le serveur acheté, avant ouverture) | Référence (valeur, machine, corpus, run) | Seuil | Condition |
|---|---|---|---|---|
| PERF-S1 | Débit soutenu de `parcours-agent.js` | 6 parcours/s, conteneur public, France entière, tir k6 n° 2 du 2026-11-02 (`perf/k6/RESULTATS-2026-11-02-TIR2.md`) | ≥ 10 parcours/s (2 × la pointe estimée de 5) | p95 ≤ 1 000 ms global et par route (dont `/v1/communes` sans filtre), échecs ≤ 1 %, aucune itération perdue |
| PERF-S2 | Fiche adresse en fond, p95 au palier 1 | 603 ms, même tir | ≤ 900 ms (× 1,5) | pendant PERF-S1 |
| PERF-S3 | Remise en place de la base nationale (transfert + restauration) | restauration seule de la base de travail : 5 615 s, hyperviseur, 2026-11-02 (dump 30,7 Go, 769 tables, `caaa9255`) | ≤ 6 h | — |
| PERF-S4 | Reconstruction complète par l'ETL depuis l'archive | ≈ 17 h cumulées sur la base de travail (1 016 min sur 21 jeux, `04.2-MESURES-NATIONALES.md`, sans BDNB) | ≤ 36 h | mesurée seulement si PERF-S3 échoue |
| PERF-S5 | Indisponibilité visible pendant une republication | coupure de la bascule nationale du 2026-11-02, non chiffrée : seuil absolu | ≤ 15 min (republication complète) ; 0 (dataset publié par échange de tables) | — |
| PERF-S6 | Sauvegarde puis restauration des comptes | aucune mesure avant le premier passage de 05-01 : seuils absolus | ≤ 5 min et ≤ 15 min | — |

Un seuil manqué ouvre un plan de fermeture d'écart (optimisation mesurée avant et après) ; il ne se déplace pas
(D-16). La pointe à 5 fiches/s n'a pas de seuil : le harnais plafonne la fiche en fond à 2/s (appels sortants vers
Géorisques et l'IGN).

## Décisions de phase 4.12

- **D-143 — Serveur MCP pour les agents** (Ianis, 2026-11-15) : le report du 2026-09-26 (« canal pour agents, à
  rouvrir après la mise en ligne si un usage se manifeste ») est levé, un service comparable exposant désormais un
  serveur MCP utilisable comme connecteur d'assistant. Ce qui est retenu :
  - **Une enveloppe de `/v1`, pas un service** : le serveur est une route `/mcp` de l'API existante (aucun
    conteneur ni processus nouveau, D-13 de la phase 5). Chaque outil rejoue en interne une route `/v1` existante
    (mêmes gardes de publication, de couverture et de délai) ; aucune lecture de données propre au serveur ;
    lecture seule.
  - **Six outils, noms et descriptions en français** : chercher une adresse, lire une adresse, comparer des
    adresses, lire une commune, comparer des communes (grandes villes et arrondissements compris ; l'échelle
    intercommunale n'est pas servie par `/v1` et n'est pas inventée), lister les jeux de données et leur couverture.
  - **Cinq états imposés par le serveur** (Ianis, 2026-11-15 : « Cinq, comme I-4 ») : chaque valeur rendue porte
    sa source, sa date (millésime ou période, « inconnue » quand elle l'est), son échelle (adresse, bâtiment,
    parcelle, rayon, commune, arrondissement, département) et un état parmi `servi`, `zero`, `manquant`,
    `hors_couverture`, `panne`, alignés sur l'invariant I-4 (zéro, non couvert, non renseigné et panne sont
    distincts). Une valeur n'existe que dans `servi` et `zero` ; `manquant`, `hors_couverture` et `panne` n'en
    portent aucune et disent leur motif (`source_indisponible` pour une panne). Une valeur de `/v1` dont la source ou
    l'échelle ne peut être établie est écartée et nommée (`ecartees`), jamais rendue comme une valeur.
  - **Ouverture** : fermé par défaut (`MCP_ENABLED=false`, 404). Ouvert, il se lit sans clé, aux plafonds par IP
    du visiteur sans clé (120 requêtes par minute, 2 000 par jour, mêmes compteurs) en plus du plafond global
    par IP. Auto-hébergeable par la même configuration. Publication (Caddy, `datacommune.fr/mcp`) : geste
    d'Ianis.
  - **Aucune dépendance d'exécution** : le transport HTTP en streaming (sans état, réponses JSON) est écrit dans
    l'application ; le client MCP officiel (`mcp`, MIT, version épinglée) n'entre que dans le groupe de
    développement, pour les tests de contrat. Réversibilité : totale tant que rien n'est publié ; ensuite, les
    noms d'outils deviennent un contrat (même politique de dépréciation que `/v1`).

## Choix techniques de phase 1

Tous datés du **2026-09-20**.

| Choix | Motif |
|---|---|
| CI = GitHub Actions `ubuntu-latest`, PostGIS par harnais **jetable** | Ne dépend d'aucun serveur du homelab ; rejoue la commande locale |
| `@sveltejs/adapter-node@5.5.7`, version exacte, intégrité relevée | Seul paquet ajouté de la phase ; mêmes mainteneurs et dépôt source que `@sveltejs/kit` ; approuvé |
| Provenance double : `3394395a` pour l'arbre, `9d903a46` pour `docs/audit-recentrage-2026-09-10/` | La spécification DPE n'existe pas à la base |
| `core.autocrlf=false` local | L'arbre transféré reste en LF comme à `3394395a` |
| `VITE_CF_BEACON_TOKEN` (Cloudflare Web Analytics) **gardé** | Dernière dépendance Cloudflare ; nommé en mesure d'audience, plus en hébergeur. Phase 5 |
| Distant **privé** pour le dépôt de travail (renommé le 2026-11-04) | D-07 n'interdit qu'un distant public tant que le suivi interne est là |
| Commits sur `master` autorisés | Dépôt à branche unique en phase 1 ; en pratique, branches de travail par session fusionnées dans `master` par l'orchestrateur, et branche `dev` prévue par D-130 amendée (2026-11-02) quand l'hyperviseur redeviendra banc de dev |
| Une entrée gitleaks `[[allowlists]]` : un chemin, une règle, une valeur exacte, avec date, motif et **preuve** | Une allowlist large aveugle le scanner en silence |
| Un test écarté sans ligne `KI-NN` n'a pas le droit d'exister | Un vert obtenu par désélection n'est pas un vert |

## Invariants

Obligations de comportement dérivées d'erreurs réelles, pas des préférences d'architecture.
Source : `DataCommune-Analyse-Contexte/08-INVARIANTS-ET-CAS.md` §1.

| # | Invariant |
|---|---|
| I-1 | **Un document n'est pas un logement.** Le lire n'établit pas l'identité du logement ; aucun état intermédiaire implicite |
| I-2 | **Une clé écrite par un tiers n'est pas une preuve d'identité.** Nommer la provenance ; une corrélation n'est pas une confirmation |
| I-3 | **Une incohérence ne doit pas devenir une absence.** « Rien », « incohérent » et « non interrogeable ici » sont trois états |
| I-4 | **Zéro, non couvert, non renseigné et panne sont quatre états.** Réparer les données ≠ corriger le code |
| I-5 | **Une valeur voyage avec son sens** : unité, période, population, définition, source, méthode. Le contrat sémantique est **acquis** |
| I-6 | **Une absence dans un fragment n'est pas un retrait.** Documents documentés et réconciliés : deux expositions |
| I-7 | **Indisponibilité explicite plutôt que mélange silencieux.** Un rollback d'image n'est pas un rollback de données |
| I-8 | **Une preuve nomme sa version et son registre** : SHA examiné, et code / données / déploiement / parcours |

## À revoir

| Sujet | Où il se tranche |
|---|---|
| Type DPE (D-06) | Phase 4 |
| Réintroduire Sentry, Cloudflare, un ordonnanceur ou Stripe | Stripe : fermé à l'ouverture (D-133) ; les autres : 05-06 |
| Ordonnanceur : l'actualisation automatique par dataset est partie avec le processus | Phase 5, avec la dépendance |
| Hébergement : Ansible/LXC ou auto-hébergé ; en-têtes statiques (KI-P3) ; sauvegarde des comptes (D-29 amendée, 02-CONTEXT) | Phase 5 |
| Panne de publication : ancienne version cohérente ou 503 borné, par parcours | Écrite en D-132 ; preuve sur la base nationale en 05-04 ; une livraison partielle ne publie rien (02-17) |
| Conservation des documents ; contenu modifié sous un même numéro DPE | Producteur, phases 2 et 4 |
| Licences : celle du code, celles des sources, restrictions DVF | Avant toute ouverture publique |
| Politique de fins de ligne (l'arbre est mixte depuis `f92d460`) | Quand elle gêne |
| Trois avis de sécurité transitifs non triés (KI-P6) | Décision de dépendances |

## Décisions reprises au bilan du 2026-11-04

Règles de l'ancien dépôt (son dossier de décisions, archivé en bundle) toujours vraies dans ce code, ou décisions prises hors des documents. Source : bilan de reprise du 2026-11-04 (suivi interne), § B4.

| # | Décision | Où elle vit |
|---|---|---|
| R-1 (ex-ADR 0003, 2026-07-03) | **Le schéma suit le code.** Toute publication migre avant la bascule, et l'API refuse de démarrer si la base n'est pas à la révision attendue | `infra/public/public-deploy.sh` (`alembic upgrade head` avant bascule, transaction unique), `api/backend/app/main.py:104` (`check_migrations_current`) |
| R-2 (ex-ADR 0004, 2026-07-03) | **Proxys fermés en cas de panne.** Une panne amont rend une indisponibilité explicite (503, `Retry-After`), jamais une réponse vide ou partielle présentée comme complète (I-4, I-7) | `api/backend/app/common/proxy.py::proxy_error_boundary`, adopté par les routeurs proxy |
| R-3 (2026-11-02, Ianis) | **But du projet : de vrais utilisateurs.** Après le cadre « portfolio » du 2026-05-29 (ex-ADR 0002), DataCommune vise une production réelle (serveur acheté fin novembre) et une prospection ciblée en Loire-Atlantique et Vendée ; le travail commercial vit dans `commercial/`, hors git | `PROJECT.md` (Key Decisions), dossier `commercial/` |

Écartées comme caduques : ex-ADR 0001 (monorepo avec historique ; ce dépôt repart sans historique), ex-ADR 0005 (CD par runner auto-hébergé ; publication par `public-deploy.sh`), ex-ADR 0006 (verdict « immeuble » ; contraire à I-1 et I-2).

---

*Problèmes connus : `docs/KNOWN-ISSUES.md` · état et preuves : `docs/REPO-STATE.md` ·
inventaire par composant : `docs/INVENTORY-3394395a.md`.*
