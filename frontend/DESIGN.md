# Design — renvoi

Ce fichier ne porte aucune règle (D-06 de la phase 4.8). Le système visuel du site public est défini par :

- **la mise en page** — jetons, échelle typographique, espacements, rayons, composants, pages : le contrat v4 de la phase 4.8 (suivi interne) (portage de la maquette du 31/10/2026, direction A) ;
- **les règles d'états, de verdicts et de rattachement** — zéro, manque, hors couverture, panne, sans objet, jamais confondus : le contrat v3 de la phase 4.7 (suivi interne), dont la v4 garde ces règles (§ 5 de la v4) ;
- les valeurs dans `src/app.css` : couleurs dans `@theme` et sous `.public-site`, jetons non colorés sur `:root` (seul fichier où une couleur s'écrit en hexadécimal) ;
- la garde `scripts/check-design-gate.mjs` (`npm run qa:design`) et ses sentinelles `scripts/qa-gates.test.mjs`, qui refusent ce qui sort du contrat.

Quand ce fichier semble dire autre chose qu'une UI-SPEC, l'UI-SPEC fait foi.

## Socle partagé (2026-11-09)

Les pièces communes des pages publiques, à brancher plutôt qu'à recopier. Ce paragraphe dit où elles sont ; leur mode d'emploi (props, classes, exemples, sélecteurs maison à retirer) est dans le suivi interne (mode d'emploi du socle, 2026-11-10).

- **Métadonnées de partage** : `src/lib/components/ds/MetaPartage.svelte` (titre, description, canonique, carte Open Graph et Twitter).
- **Bouton** : classes `.bouton-primaire`, `.bouton-secondaire`, `.bouton-lien` de `src/app.css`, sur un `<button>` comme sur un `<a>` ; rendu des boutons de la fiche commune.
- **Repli** : classes `.depliant`, `.depliant-accent`, `.depliant-cadre` de `src/app.css`, sur un `<details>` ; chevron, cible de 44 px, anneau de focus du contrat.
- **Page d'erreur** : `src/lib/components/ds/PageErreur.svelte`, sur `PageSimple`, `Notice` et `ListeLiens`.
- **Pied** : il finit au bas de la fenêtre sur une page plus courte qu'elle (`.public-site > footer` dans `src/app.css`).
- **Sigles** : `src/lib/utils/sigles.ts` ; une page ne passe à `developperSigles` que de la prose, jamais un titre, un bouton ni une cellule.

Gardes : `tests/e2e/socle-partage.spec.ts` (bouton et repli comparés aux éléments de référence), `tests/e2e/forme-publique.spec.ts` (rien sous le pied), `ds/MetaPartage.test.ts`, `ds/PageErreur.test.ts`, `sigles.test.ts`, `tests/e2e/sigles.spec.ts`.

Historique : les contrats antérieurs (B · Sauge du 29/10/2026, UI-SPEC de la phase 3, ancien registre « Marbre & Bronze ») restent lisibles dans le dépôt et dans l'historique git ; la v4 garde les douze couleurs Sauge (§ 1 de la v4).
