# Phase 57 — Dossier de copie DVF, prêt à trancher

**Date :** 2026-08-25
**Plan d'origine :** 57-14 (Task 3)
**Processus :** D-19 (Phase 57) selon le mécanisme D-21 de la Phase 56 — Claude propose, Ianis
tranche mot à mot. Registre document : factuel, zéro formule marketing, aucune promesse de
complétude.
**Application :** plan 57-15.

## Comment lire ce dossier

La copie **est déjà écrite dans le code**, et elle est **PROVISOIRE**. Elle vit dans UNE seule
constante de module, `COPIE_DVF_PROVISOIRE`
(`frontend/src/lib/data/dvf-attribution-view.ts`), consommée par `bien-fiche.ts` et
`commune-fiche.ts` — aucune chaîne DVF n'est dispersée ailleurs. Le remplacement décidé ici sera
donc **mot pour mot et mécanique** : une clé du dossier = une clé de la constante.

Chaque ligne porte quatre colonnes :

| Colonne | Contenu |
|---|---|
| Identifiant technique | La clé du vocabulaire fermé backend. **Jamais affichée** — elle sert à retrouver la ligne. |
| Contexte d'affichage | Où le lecteur voit ce texte, et à quel moment. |
| Proposition exacte | Le texte proposé, au caractère près. |
| Arbitrage | À remplir : `OK`, ou la formulation retenue. |

Deux règles tenues dans toutes les propositions ci-dessous :

1. **Aucun identifiant technique ne va à l'écran.** `compatible_parcelle`,
   `residentiel_appartement`, `vefa_sans_surface` n'apparaissent nulle part dans le DOM public —
   le gate `tests/e2e/honnetete-publique.spec.ts` le vérifie déjà pour les identifiants d'état.
2. **Une absence est expliquée, jamais muette, et jamais confondue avec une panne** (D-16). Les
   deux dernières lignes de la famille 2 décrivent des indisponibilités TECHNIQUES : elles ne
   doivent jamais pouvoir se lire comme « aucune vente ici ».

---

## Famille 0 — Le libellé du KPI de prix (D-37)

Le libellé actuel affirme une attribution : « dernière vente » sous-entend « de ce bien », ce que
la Phase 57 établit précisément comme non prouvé. Il change donc, et le changement est consigné
ici parce qu'il est visible sur toutes les Fiches Bien.

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `kpiLabel` — **ancien** | Fiche Bien, synthèse, 2ᵉ KPI (libellé au-dessus de la valeur) | ~~Prix au m² (dernière vente)~~ — retiré : affirme une attribution | Retiré — confirmé (Ianis, 2026-08-25) |
| `kpiLabel` — **nouveau** | idem | `Prix au m² (transaction sur la parcelle)` | **Remplacé (Ianis, 2026-08-25 soir, D-46)** : `Prix au m² sur la parcelle` — supersède le remplacement Codex `Prix au m² d’une transaction compatible sur la parcelle`, écarté |

**Note d'emplacement (à trancher aussi).** La ligne d'état accompagnant ce KPI est rendue dans la
pastille du KPI (l'emplacement qui portait jusqu'ici une position comparative). C'est le seul
emplacement d'une ligne **toujours visible** sous la valeur, et D-21 interdit de restructurer la
Fiche pour en créer un autre. La phrase longue, elle, est rendue par le panneau ℹ️ du même KPI.
Si l'arbitrage préfère un autre emplacement, il faudra rouvrir D-21 — ce n'est plus une question
de mots.

**Arbitrage (Ianis, 2026-08-25) :** emplacement retenu tel que proposé (pastille du KPI), D-21 non rouverte.

---

## Famille 1 — Étiquettes de classe de mutation

Une ligne par valeur du vocabulaire fermé `app.etl_dvf.qualification.CLASSES_MUTATION` (7), plus
le cas non qualifié et le suffixe des ventes sur plan. Ces étiquettes apparaissent dans
l'historique de parcelle de la Fiche Bien (« Nature de la transaction », puis une ligne par
transaction antérieure).

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `residentiel_maison` | Fiche Bien, thème Marché & prix, nature de la transaction | `Vente d'une maison seule` | Retenu tel quel (Ianis, 2026-08-25) |
| `residentiel_appartement` | idem | `Vente d'un appartement seul` | Retenu tel quel (Ianis, 2026-08-25) |
| `ensemble_residentiel` | idem | `Vente de plusieurs logements ensemble, à prix global` | Retenu tel quel (Ianis, 2026-08-25) |
| `mixte` | idem | `Vente mêlant logement et local d'activité, à prix global` | Retenu tel quel (Ianis, 2026-08-25) |
| `commerciale` | idem | `Vente de locaux d'activité` | Retenu tel quel (Ianis, 2026-08-25) |
| `terrain_seul` | idem | `Vente de terrain sans bâti` | Retenu tel quel (Ianis, 2026-08-25) |
| `hors_perimetre` | idem | `Transaction hors du périmètre publié` | Retenu tel quel (Ianis, 2026-08-25) |
| `inconnue` (classe absente ou hors vocabulaire) | idem | `Nature de la transaction non qualifiée` | Retenu tel quel (Ianis, 2026-08-25) |
| `suffixeVefa` | idem, **accolé** à l'étiquette de classe entre parenthèses, jamais rendu seul | `vente sur plan (état futur d'achèvement)` | **Remplacé (Ianis, 2026-08-25 soir, D-46)** : `vente sur plan` — rendu `{étiquette} ({suffixe})`, donc plus de tiret cadratin et plus de parenthèse imbriquée |

**Point d'attention.** « local d'activité » est proposé plutôt que « local commercial » : la
catégorie DGFiP couvre l'industriel et assimilés, pas seulement le commerce. À trancher, c'est un
arbitrage entre exactitude et familiarité.

**Arbitrage (Ianis, 2026-08-25) :** « local d'activité » retenu tel que proposé.

---

## Famille 2 — Raisons d'absence du KPI

Une ligne par clé du vocabulaire fermé `app.dvf.schemas.DVF_ATTRIBUTION_RAISONS` (12), plus les
deux états positifs. `mention` est la **ligne unique** que voit quelqu'un dont le bien n'a pas de
vente attribuable ; `detail` est la phrase du panneau ℹ️.

### 2a — États positifs

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `etats.compatible_parcelle.mention` | Fiche Bien, sous la valeur du KPI de prix — le prix EST affiché | `Transaction non attribuée à ce bien` | **Remplacé (Ianis, 2026-08-25 soir, D-46)** : `Non attribuée` — supersède le remplacement Codex `Transaction compatible sur la parcelle — non attribuée au logement`, écarté |
| `etats.compatible_parcelle.detail` | panneau ℹ️ du même KPI | `Transaction compatible observée sur cette parcelle — non attribuée au bien. Sans preuve d'identité du logement vendu, ce prix reste celui d'une transaction de la parcelle, jamais le prix certain de ce logement.` | **Remplacé (Ianis, 2026-08-25 soir, D-46)** : `Transaction compatible observée sur cette parcelle, non attribuée au bien. Sans preuve d'identité du logement vendu, ce prix reste celui d'une transaction de la parcelle, jamais le prix certain de ce logement.` — seul le tiret cadratin devient une virgule |
| `etats.exacte.mention` | idem — **état non produit à ce jour** (D-37), écrit pour le jour où une preuve locale existera | `Vente rattachée à ce bien` | Retenu tel quel (Ianis, 2026-08-25) |
| `etats.exacte.detail` | panneau ℹ️ | `Cette vente est rattachée à ce bien par une preuve d'identité du logement vendu.` | Retenu tel quel (Ianis, 2026-08-25) |

### 2b — Absences métier (une mutation existe, rien n'est attribuable)

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `derniere_vente_mixte` — mention | KPI de prix, valeur `n.d.` | `Vente mêlant logement et local d'activité` | Retenu tel quel (Ianis, 2026-08-25) |
| `derniere_vente_mixte` — detail | panneau ℹ️ | `La transaction la plus récente mêle un logement et un local d'activité, vendus à prix global : ce prix ne peut pas être rapporté à ce bien.` | Retenu tel quel (Ianis, 2026-08-25) |
| `derniere_vente_commerciale` — mention | KPI, `n.d.` | `Vente de locaux d'activité` | Retenu tel quel (Ianis, 2026-08-25) |
| `derniere_vente_commerciale` — detail | ℹ️ | `La transaction la plus récente ne porte que des locaux d'activité : elle ne concerne pas ce logement.` | Retenu tel quel (Ianis, 2026-08-25) |
| `vente_ensemble` — mention | KPI, `n.d.` | `Plusieurs logements vendus ensemble` | Retenu tel quel (Ianis, 2026-08-25) |
| `vente_ensemble` — detail | ℹ️ | `La transaction la plus récente porte plusieurs logements vendus ensemble, à prix global : ce prix n'est celui d'aucun logement pris séparément.` | Retenu tel quel (Ianis, 2026-08-25) |
| `type_incompatible` — mention | KPI, `n.d.` | `Vente d'un autre type de bien` | Retenu tel quel (Ianis, 2026-08-25) |
| `type_incompatible` — detail | ℹ️ | `La transaction la plus récente porte un type de bien différent de celui consulté : une vente de maison n'est jamais rapportée à un appartement, ni l'inverse.` | Retenu tel quel (Ianis, 2026-08-25) |
| `classe_non_residentielle` — mention | KPI, `n.d.` | `Vente hors logement unique` | Retenu tel quel (Ianis, 2026-08-25) |
| `classe_non_residentielle` — detail | ℹ️ | `La transaction la plus récente n'est pas la vente d'un logement unique : aucun prix ne lui est rapporté.` | Retenu tel quel (Ianis, 2026-08-25) |
| `vefa_sans_surface` — mention | KPI, `n.d.` | `Vente sur plan, surface non fournie` | Retenu tel quel (Ianis, 2026-08-25) |
| `vefa_sans_surface` — detail | ℹ️ | `Vente en l'état futur d'achèvement (achat sur plan) : la surface n'est pas fournie par le fichier des ventes, aucun prix au mètre carré n'est calculable.` | Retenu tel quel (Ianis, 2026-08-25) |
| `vente_sur_plan` — mention | KPI, `n.d.` — raison NOUVELLE (D-47) : la mutation la plus récente est une vente sur plan **avec** surface ; sans surface, c'est `vefa_sans_surface` | `Vente sur plan` | Retenu (Ianis, 2026-08-25 soir, D-47) |
| `vente_sur_plan` — detail | ℹ️ | `Vente en l'état futur d'achèvement (achat sur plan) : marché du neuf, non comparable à l'ancien, aucun prix au m² retenu.` | Retenu (Ianis, 2026-08-25 soir, D-47) |
| `nature_hors_vente` — mention | KPI, `n.d.` | `Transaction qui n'est pas une vente` | Retenu tel quel (Ianis, 2026-08-25) |
| `nature_hors_vente` — detail | ℹ️ | `La transaction la plus récente n'est pas une vente au sens du périmètre publié (adjudication, échange, expropriation).` | Retenu tel quel (Ianis, 2026-08-25) |
| `type_bien_inconnu` — mention | KPI, `n.d.` | `Type du bien non déterminé` | Retenu tel quel (Ianis, 2026-08-25) |
| `type_bien_inconnu` — detail | ℹ️ | `Le type du bien consulté n'est pas déterminé : aucune compatibilité ne peut être établie avec les transactions de la parcelle, et aucune n'est retenue au hasard.` | Retenu tel quel (Ianis, 2026-08-25) |
| `aucune_mutation` — mention | KPI, `n.d.` | `Aucune vente enregistrée sur cette parcelle` | Retenu tel quel (Ianis, 2026-08-25) |
| `aucune_mutation` — detail | ℹ️ | `Aucune vente n'est enregistrée sur cette parcelle dans le fichier public des ventes.` | Retenu tel quel (Ianis, 2026-08-25) |
| `parcelle_indisponible` — mention | KPI, `n.d.` | `Aucune parcelle cadastrale résolue` | Retenu tel quel (Ianis, 2026-08-25) |
| `parcelle_indisponible` — detail | ℹ️ | `Aucune parcelle cadastrale n'a pu être résolue pour cette adresse : aucune vente n'a été cherchée.` | Retenu tel quel (Ianis, 2026-08-25) |

**`type_bien_inconnu` mérite une attention particulière.** C'est l'état que `/v1/resolve` produit
**aujourd'hui pour toute parcelle portant des mutations** : la résolution ne transporte pas encore
le type du bien consulté, donc la compatibilité de type (D-10) ne peut pas être établie et la
politique refuse d'attribuer au hasard. C'est donc la phrase la plus lue de toute cette famille
tant que la résolution ne porte pas le type. Elle doit rester compréhensible par quelqu'un qui ne
sait pas ce qu'est une « résolution » — la proposition ci-dessus dit « le type du bien consulté
n'est pas déterminé », pas « la résolution n'a pas abouti ». À trancher en connaissance de cette
fréquence.

**Arbitrage (Ianis, 2026-08-25) :** formulation retenue telle quelle ; la fréquence de cet état est traitée
par le plan 57-18 (type de bien explicite fourni par l'utilisateur), pas par la copie.

### 2c — Indisponibilités techniques (jamais une absence de vente)

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `entree_illisible` — mention | KPI, `n.d.` | `Données de vente indisponibles` | Retenu tel quel (Ianis, 2026-08-25) |
| `entree_illisible` — detail | ℹ️ | `Les données de vente n'ont pas pu être lues. Cette indisponibilité est technique : elle ne signifie pas qu'aucune vente n'a eu lieu ici.` | Retenu tel quel (Ianis, 2026-08-25) |
| `temporary_error` — mention | KPI, `n.d.` — **aussi** l'état affiché quand la réponse ne porte aucune décision | `Données de vente momentanément indisponibles` | Retenu tel quel (Ianis, 2026-08-25) |
| `temporary_error` — detail | ℹ️ | `Les données de vente sont momentanément indisponibles. Réessayez plus tard ; les autres informations de la fiche restent disponibles.` | Retenu tel quel (Ianis, 2026-08-25) |

---

## Famille 3 — Mentions de limite (D-18)

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| Limite VEFA, Fiche Bien | Portée par la raison `vefa_sans_surface` ci-dessus — pas de seconde mention, une seule phrase dit la cause et la conséquence | `Vente en l'état futur d'achèvement (achat sur plan) : la surface n'est pas fournie par le fichier des ventes, aucun prix au mètre carré n'est calculable.` | Retenu tel quel (Ianis, 2026-08-25) |
| Limite VEFA, statistiques commune | Portée par le libellé des 3 lignes de prix (famille 4) + développée dans le panneau ℹ️ de chaque ligne | `hors VEFA` dans le libellé ; dans l’ℹ️ : `Les ventes en l’état futur d’achèvement (VEFA, achat sur plan) sont exclues de cet indicateur afin de ne pas mélanger les ventes dans le neuf et dans l’ancien.` | **Remplacé (Ianis, 2026-08-25, correctif final)** : « `Les ventes en l’état futur d’achèvement (VEFA, achat sur plan) sont exclues de cet indicateur afin de ne pas mélanger les ventes dans le neuf et dans l’ancien.` » — l’ancienne rédaction invoquait une couverture du neuf partielle jamais mesurée ; le libellé visible reste `hors ventes sur plan` (au lieu de `hors VEFA`), `VEFA` expliqué dans l’infobulle |
| Seuil de non-publication d'une médiane | Panneau ℹ️ des lignes d'effectif de la Fiche Commune | `Sous un seuil de publication, le prix médian n'est pas affiché : il désignerait une transaction identifiable.` | Retenu tel quel (Ianis, 2026-08-25) |

**Décision d'emplacement à confirmer.** La mention « hors VEFA » est portée par le **libellé** de
la ligne et non par une réserve dédiée : la structure actuelle de la Fiche Commune n'offre pas
d'emplacement de réserve, et D-21 interdit d'en créer un en Phase 57. L'acronyme VEFA reste donc
nu dans le libellé, développé dans l'ℹ️. L'alternative — écrire « hors ventes sur plan » dans le
libellé et réserver VEFA à l'ℹ️ — est plus lisible mais s'éloigne du vocabulaire de la source.
À trancher.

**Arbitrage (Codex, validé Ianis 2026-08-25) :** libellé visible « hors ventes sur plan » ; « VEFA » expliqué dans l'infobulle.

---

## Famille 4 — Libellés des lignes de Fiche

### 4a — Fiche Commune, thème Marché & prix

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `lignesCommune.prixMedian` | ligne existante, libellé amendé (ancien : `Prix médian de vente`) | `Prix médian de vente (hors VEFA)` | **Remplacé (Codex, validé Ianis 2026-08-25)** : `Prix médian de vente (hors ventes sur plan)` |
| `lignesCommune.prixMedianMaison` | ligne nouvelle | `Prix médian maison (hors VEFA)` | **Remplacé (Codex, validé Ianis 2026-08-25)** : `Prix médian maison (hors ventes sur plan)` |
| `lignesCommune.ventesMaison` | ligne nouvelle | `Ventes de maisons sur 12 mois` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesCommune.prixMedianAppartement` | ligne nouvelle | `Prix médian appartement (hors VEFA)` | **Remplacé (Codex, validé Ianis 2026-08-25)** : `Prix médian appartement (hors ventes sur plan)` |
| `lignesCommune.ventesAppartement` | ligne nouvelle | `Ventes d'appartements sur 12 mois` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesCommune.mutations12m` | ligne existante, inchangée | `Mutations sur 12 mois` | Retenu tel quel (Ianis, 2026-08-25) |

### 4b — Fiche Bien, thème Marché & prix (« sur ce bien »)

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `lignesBien.classe` | ligne nouvelle, en tête du bloc | `Nature de la transaction` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.valeurFonciere` | ligne existante, libellé amendé (ancien : `Valeur foncière (dernière vente)`) | `Valeur foncière de la transaction` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.prixM2` | inchangé | `Prix au m²` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.date` | inchangé | `Date de mutation` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.typeLocal` | inchangé | `Type de local` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.surfaceBatiTotale` | ligne nouvelle | `Surface bâtie de la transaction` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.surfaceReelle` | inchangé | `Surface réelle bâtie` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.pieces` | inchangé | `Nombre de pièces` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.historique` | ligne nouvelle, répétée une fois par transaction antérieure ; la valeur est `{date} · {étiquette de classe}` (séparateur point médian depuis D-46) | `Autre transaction sur la parcelle` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.gapPrix` | inchangé — libellé du bloc quand le jeu de données n'a pas répondu | `Prix médian au m²` | Retenu tel quel (Ianis, 2026-08-25) |
| `lignesBien.gapDate` | inchangé — idem | `Dernière mutation` | Retenu tel quel (Ianis, 2026-08-25) |

**Deux libellés hérités signalés, non corrigés ici.** `gapPrix` (« Prix médian au m² ») et
`gapDate` (« Dernière mutation ») ne correspondent pas aux libellés du chemin nominal (« Prix au
m² », « Date de mutation ») : la même donnée change de nom selon qu'elle est disponible ou non.
C'est une incohérence antérieure à la Phase 57, laissée en place pour ne pas élargir le
périmètre. À trancher : les aligner, ou les garder.

**Arbitrage (Ianis, 2026-08-25) :** gardés tels quels (hors périmètre, pas de correction au passage).

**Montant des transactions antérieures.** La ligne `historique` porte la date et l'étiquette,
**pas le montant**. Un prix global n'a pas de lecture au niveau du bien, et la tranche de
minimisation (D-03) ne couvre que la transaction la plus récente. À confirmer.

**Arbitrage (Ianis, 2026-08-25) :** confirmé — date et étiquette, pas de montant.

---

## Famille 5 — Section « limites de la source » de la page dataset DVF (D-20)

Proposée ici, **appliquée au plan 57-15** sur la page dataset DVF du site public. Elle solde
l'action n° 2 du backlog Reddit. Registre document, à la troisième personne, sans excuse ni
promesse.

| Identifiant technique | Contexte d'affichage | Proposition exacte | Arbitrage |
|---|---|---|---|
| `limites.titre` | Titre de la section, page dataset DVF | `Limites de la source` | Retenu tel quel (Ianis, 2026-08-25) |
| `limites.chapeau` | Phrase d'introduction | `Le fichier des demandes de valeurs foncières (DVF) est publié par la direction générale des finances publiques. Il ne couvre pas tout le territoire ni tout le marché ; ces limites sont celles de la source, et elles ne sont pas comblées.` | Retenu tel quel (Ianis, 2026-08-25) |
| `limites.alsace_moselle` | Puce 1 | `Bas-Rhin, Haut-Rhin et Moselle ne sont pas couverts : ces départements relèvent du livre foncier, un régime de publicité foncière distinct, et leurs transactions ne figurent pas dans DVF.` | Retenu tel quel (Ianis, 2026-08-25) |
| `limites.mayotte` | Puce 2 | `Mayotte n'est pas couverte.` | Retenu tel quel (Ianis, 2026-08-25) |
| `limites.neuf` | Puce 3 | `La couverture du neuf est partielle : les ventes en l'état futur d'achèvement (VEFA, achat sur plan) y figurent de façon incomplète et sans surface exploitable. Elles sont exclues des statistiques de prix publiées ici, et restent visibles dans les historiques, étiquetées.` | **Remplacé (Codex, validé Ianis 2026-08-25)** : `Les ventes en l’état futur d’achèvement (VEFA) sont présentes dans DVF, mais certaines ne comportent pas de descriptif du local ou de surface exploitable. DataCommune les conserve dans l’historique, les étiquette et les exclut de ses statistiques de prix afin de ne pas mélanger neuf et ancien.` |
| `limites.delai` | Puce 4 | `Les ventes sont publiées avec plusieurs mois de décalage : une transaction récente peut ne pas encore y figurer.` | **Remplacé (Codex, validé Ianis 2026-08-25)** : `DVF est actualisé deux fois par an, en avril et en octobre. Une transaction récente ou enregistrée tardivement peut ne pas encore apparaître, et des millésimes antérieurs peuvent être complétés lors d’une mise à jour.` |
| `limites.declaratif` | Puce 5 | `Les surfaces et le nombre de pièces sont déclaratifs et peuvent différer des mesures d'un diagnostic.` | **Remplacé (Codex, validé Ianis 2026-08-25)** : `Les surfaces et le nombre de pièces proviennent d’informations fiscales et cadastrales ; leur définition peut différer de celle utilisée dans un diagnostic énergétique.` |

**À vérifier avant publication** (ce dossier propose une formulation, pas un fait établi) : la
liste exacte des territoires non couverts au millésime servi. Les trois départements
d'Alsace-Moselle et Mayotte sont les exclusions constantes de la publication ; si le millésime
servi en porte d'autres, la puce doit les nommer plutôt que rester générale.

**Arbitrage (Codex, validé Ianis 2026-08-25) :** exclusions Bas-Rhin, Haut-Rhin, Moselle et Mayotte confirmées ;
cadence semestrielle (avril/octobre) confirmée par la publication officielle DVF (data.gouv.fr).

---

## Ce que ce dossier ne couvre PAS

- **La superficie affichée sur une Fiche maison** : D-38 la donne à la Phase 58, aucune bascule
  n'est proposée ici.
- **L'historique immeuble** (`VenteImmeuble`, plan 57-13) : sa copie a été tranchée avec le plan
  qui l'a produit et n'est pas rouverte.
- **Les seuils chiffrés** de non-publication d'une médiane : ils relèvent de D-23/D-24 et du
  rapport de mesure, pas d'un arbitrage de vocabulaire.

---

## Arbitrage rendu le 2026-08-25 par Ianis

Décision : **toutes les formulations du dossier sont validées telles quelles**, à l'exception des
remplacements explicitement donnés par Codex (revue du 2026-08-25) et du correctif final
rendu par Ianis le 2026-08-25 sur l’infobulle VEFA des indicateurs de prix commune, consignés ci-dessus mot pour
mot dans la colonne « Arbitrage » et rappelés ici :

| Clé | Formulation retenue (verbatim Codex) |
|---|---|
| `kpiLabel` | `Prix au m² d’une transaction compatible sur la parcelle` |
| `etats.compatible_parcelle.mention` | `Transaction compatible sur la parcelle — non attribuée au logement` |
| `limites.neuf` | `Les ventes en l’état futur d’achèvement (VEFA) sont présentes dans DVF, mais certaines ne comportent pas de descriptif du local ou de surface exploitable. DataCommune les conserve dans l’historique, les étiquette et les exclut de ses statistiques de prix afin de ne pas mélanger neuf et ancien.` |
| `limites.delai` | `DVF est actualisé deux fois par an, en avril et en octobre. Une transaction récente ou enregistrée tardivement peut ne pas encore apparaître, et des millésimes antérieurs peuvent être complétés lors d’une mise à jour.` |
| `limites.declaratif` | `Les surfaces et le nombre de pièces proviennent d’informations fiscales et cadastrales ; leur définition peut différer de celle utilisée dans un diagnostic énergétique.` |
| Libellés visibles « hors VEFA » (famille 3 + 4a) | `hors ventes sur plan` dans le libellé ; `VEFA` expliqué dans l'infobulle |
| Infobulle VEFA des 3 lignes de prix commune (famille 3) | `Les ventes en l’état futur d’achèvement (VEFA, achat sur plan) sont exclues de cet indicateur afin de ne pas mélanger les ventes dans le neuf et dans l’ancien.` |

Application : plan 57-15 Task 2 (code + test d'application lisant ce dossier) et Task 3 (section
« limites de la source »), **dans le même commit** que le réalignement des littéraux provisoires de
`frontend/tests/e2e/dvf-attribution.spec.ts` (plan 57-16). Aucune autre reformulation n'est autorisée.

---

## Arbitrage complémentaire rendu le 2026-08-25 (soir) par Ianis — D-46 / D-47

Rendu après l'UAT en production (gap-closure R3 du plan 57-17). Il **supersède** les
formulations correspondantes du récapitulatif ci-dessus : là où une clé apparaît dans les deux
tables, c'est la table de cette section qui fait foi.

**D-46 — copie courte.** Aucune chaîne DVF **affichée** ne porte plus de tiret cadratin « — ».
La règle est absolue et vérifiable : elle porte sur les littéraux rendus (`COPIE_DVF`, valeur des
lignes d'historique, entrées `dvf.*` / `bien.dvf.*` de `explications.ts`), **pas** sur les
commentaires de code, où le tiret cadratin reste autorisé. Selon le cas, il devient une
parenthèse, un point médian « · » ou une virgule — jamais une suppression qui changerait le sens.

**D-47 — nouvelle raison `vente_sur_plan`.** Statut `non_attribuable`, KPI `n.d.` comme les
autres raisons. Elle couvre le cas « la mutation la plus récente est une vente sur plan **avec**
surface » : le prix au m² serait calculable, mais il relève du marché du neuf et n'est pas
comparable à l'ancien. Sans surface, la raison reste `vefa_sans_surface`. Le rendu suit le patron
exact des autres raisons dans le `switch` exhaustif de `deriveDvfAttributionView`.

| Clé | Formulation retenue |
|---|---|
| `kpiLabel` | `Prix au m² sur la parcelle` |
| `etats.compatible_parcelle.mention` | `Non attribuée` |
| `etats.compatible_parcelle.detail` | `Transaction compatible observée sur cette parcelle, non attribuée au bien. Sans preuve d'identité du logement vendu, ce prix reste celui d'une transaction de la parcelle, jamais le prix certain de ce logement.` |
| `suffixeVefa` | `vente sur plan` — rendu `{étiquette} ({suffixe})`, soit `Vente d'un appartement seul (vente sur plan)` |
| `lignesBien.historique` — valeur rendue | `{date} · {étiquette de classe}`, soit `01/10/2025 · Vente d'un appartement seul` |
| `raisons.vente_sur_plan` — mention | `Vente sur plan` |
| `raisons.vente_sur_plan` — detail | `Vente en l'état futur d'achèvement (achat sur plan) : marché du neuf, non comparable à l'ancien, aucun prix au m² retenu.` |

**Formulations retirées par D-46** — aucune ne doit subsister dans la copie exécutable, et le test
d'application rougit si l'une revient :

- `Prix au m² d'une transaction compatible sur la parcelle`
- `Transaction compatible sur la parcelle — non attribuée au logement`
- `Transaction compatible observée sur cette parcelle — non attribuée au bien.`
- `vente sur plan (état futur d'achèvement)`

**Deux ℹ️ DVF de `explications.ts` alignés sur D-46** (même règle, même commit) : le tiret cadratin
de `dvf.prix_m2_median` (« … issu des actes notariés — des prix réellement signés … ») et celui de
`bien.dvf.valeur_fonciere` (« … plusieurs lots vendus ensemble — un appartement et son garage … »)
passent entre parenthèses. Aucun mot n'est ajouté ni retiré.

**Déjà appliquée, rien à faire :** la phrase de seuil de la famille 3 (« Sous un seuil de
publication, le prix médian n'est pas affiché : il désignerait une transaction identifiable. »)
est présente depuis 57-15 dans l'ℹ️ des deux lignes d'effectif de la Fiche Commune
(`dvf.nb_mutations_12m_maison`, `dvf.nb_mutations_12m_appartement`).
