# DataCommune — consignes pour les agents et les contributeurs

- Communication en français ; code, identifiants, commentaires et messages de commit en anglais.
- Stack : Python/FastAPI, PostgreSQL/PostGIS, SvelteKit. Une dépendance nouvelle se justifie par un
  usage réel ; aucune couche, aucun framework sans besoin concret.
- Ne pas confondre document DPE, adresse, bâtiment, parcelle et logement. Garder distincts zéro,
  manque, hors couverture et panne.
- Annoncer le résultat attendu et son contrôle avant un changement ; réutiliser les commandes du
  projet (`scripts/test-*.ps1`). Mesurer avant et après une optimisation, sur les mêmes entrées.
- La procédure locale est unique : section « Procédure locale » du `README.md` ; ne pas en écrire
  une deuxième.
- Tests web uniquement par Playwright, sans interface et en contexte isolé ; arrêter les
  ressources créées.
- Secrets hors dépôt, `.env` et `*.local.env` ignorés. Aucune donnée réelle identifiante dans les
  tests (une adresse avec une vente, un nom, un courriel).
- Une preuve nomme la commande, l'environnement, la version et les entrées. Distinguer code,
  données, contrôle local, CI et déploiement.
- Contributions : `CONTRIBUTING.md` (branches, DCO). Le dépôt de travail du mainteneur ajoute ses
  propres consignes dans son suivi interne, absent de ce dépôt.
