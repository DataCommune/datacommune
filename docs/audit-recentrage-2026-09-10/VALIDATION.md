# Contrôles du dossier préparé

Date : 10 septembre 2026. Ce reçu concerne le plan et ses preuves, pas une correction applicative.

- `check_evidence.py evidence` : succès sur 25 empreintes, 16 résolutions, 32 requêtes ADEME principales, 14 comptes au même BAN, 157 projections de cinq champs, quatre consultations de détail, six pages / 114 références pour R14, version préproduction stable.
- Contrôle négatif : copie temporaire des preuves, altération de `R14-details.json` (`items_read=113`) ; le vérificateur échoue comme attendu. Les preuves originales restent inchangées.
- Documentation : liens locaux relatifs vérifiés, espaces finaux absents, délimiteurs Markdown équilibrés ; 27 schémas Mermaid présents. Leur syntaxe n'a pas été passée dans un moteur Mermaid pendant ce contrôle.
- Captures JSON : aucune clé d'accès ni référence signée conservée dans les champs contrôlés. Les identifiants de diagnostics ADEME et adresses des cas sont des données sources publiques, sans identité de résident.
- Schéma interactif : 13 fonctionnalités × 2 états parcourus dans le navigateur, quatre nœuds rendus par état. Capture visuelle desktop et à 375 px ; pour la vue DPE mobile, toutes les étiquettes sont dans les limites du diagramme. Les autres vues ont été contrôlées fonctionnellement, pas photographiées une par une à toutes les largeurs.
- Revue indépendante du plan : intégrité des preuves, séparation document/sélection/attribution, recherche complémentaire bornée, textes libres, validation de l'automatisation. Corrections intégrées au plan et au vérificateur.
- Périmètre d'écriture : nouveau dossier documentaire `docs/audit-recentrage-2026-09-10/` et schéma dans le répertoire de visualisation de cette tâche. Aucun changement applicatif, migration, commit, déploiement ou modification de roadmap.

Le dossier distingue observations fraîches de préproduction, preuves antérieures de phases, faits de code, propositions et validations encore à réaliser. Les pièces restent locales et non commitées.
