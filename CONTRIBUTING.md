# Contribuer à DataCommune

Merci de votre intérêt. Ce document dit comment proposer une modification ; la documentation et
les échanges sont en français, une issue ou une pull request en anglais est acceptée.

## Avant de commencer

- Un bug, un écart avec une source, une idée : ouvrez d'abord une **issue** (modèles « Bug »,
  « Écart de donnée », « Proposition ») ou une **discussion**. Pour une faille de sécurité, suivez
  `SECURITY.md` et n'ouvrez pas d'issue publique.
- Une issue étiquetée `bonne première contribution` est un bon point de départ.

## Installer et tester

La procédure est unique : section « Procédure locale » du `README.md` (prérequis, environnement,
commandes). Avant d'ouvrir une pull request, jouez au moins les suites touchées par votre
modification :

- API : `scripts/test-backend.ps1` (lint, contrats, tests sans base) et, si vous touchez aux
  requêtes, `scripts/test-postgis.ps1` ;
- site : `scripts/test-frontend.ps1 -Gate all` (contrôles, vérification de types, tests unitaires,
  build) ; les tests de navigateur passent par `scripts/test-e2e.ps1` (Playwright, sans
  interface).

Sur chaque pull request, la CI rejoue les suites concernées par les fichiers touchés, vérifie la
signature `Signed-off-by` et cherche des secrets.

## Branches et commits

- Une branche par sujet, courte, nommée `feat/…`, `fix/…`, `data/<source>`, `docs/…`, `infra/…` ou
  `chore/…` (minuscules et tirets).
- Messages de commit en anglais, au format `type(portée): résumé` (par exemple
  `fix(adresse): keep the parcel when the point is on its edge`).
- Une pull request décrit ce qui change et comment c'est vérifié (commande lancée, résultat). Elle
  est fusionnée par *squash* sur `master` une fois la CI verte.

## Signature des contributions (DCO)

DataCommune n'utilise pas d'accord de cession (CLA). Chaque commit d'une contribution doit porter
une ligne `Signed-off-by` qui atteste le **Developer Certificate of Origin 1.1**
(<https://developercertificate.org/>) : vous certifiez avoir le droit de proposer ce code sous la
licence du projet. Git l'ajoute avec l'option `-s` :

```bash
git commit -s -m "fix(adresse): …"
```

Pour signer des commits déjà faits : `git rebase --signoff master`. Un contrôle automatique refuse
une pull request dont un commit n'est pas signé.

## Licence

En contribuant, vous acceptez que votre contribution soit diffusée sous la licence du projet,
GNU Affero General Public License v3.0 ou toute version ultérieure (`AGPL-3.0-or-later`).

## Comment une contribution entre dans le projet

Le dépôt public est la référence du code. Le mainteneur travaille aussi dans un dépôt privé qui
porte, en plus, le suivi interne et les réglages de ses machines ; une pull request fusionnée ici
y est reportée telle quelle, avec son auteur et sa signature.
