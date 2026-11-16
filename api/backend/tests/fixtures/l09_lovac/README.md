# LOVAC intégral figé — L09

Copie compressée déterministe du fichier officiel déjà conservé par COR08 dans `C:/tmp/cor08-lovac-official.csv`. Aucun téléchargement L09. Source communale publique uniquement ; aucune adresse ni donnée de dossier individuel.

- CSV latin-1, séparateur `;`, 7 628 201 octets ; SHA256 `b856568d4b69784d32f9fafaf7e1376047e45eed245fb114dd737d2d43826245`.
- 34 912 lignes et codes distincts, numérateur 2026 / dénominateur 2025.
- 18 321 dénominateurs sous secret statistique (`s`), 19 taux observés zéro. Le secret statistique ne représente pas zéro.
- Gzip niveau9, mtime0, sans nom de fichier ; 1 168 493 octets. `.gitattributes` interdit la conversion texte. Les deux hashes et tailles figurent dans `manifest.json`.
- Décompactage limité à taille attendue+1, vérification taille et SHA avant parsing ; aucun téléchargement de secours ou skip.

L’oracle indépendant `tests/oracles/lovac_full.py` utilise CSV et Decimal, sans importer les transformations applicatives. Les reprises se font exclusivement en base enfant du conteneur PostGIS jetable créé par le wrapper. Aucune affirmation de couverture hors fichier ni de réparation d’une base partagée.
