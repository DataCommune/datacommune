# Sécurité

## Signaler une faille

Ne publiez pas une faille dans une issue, une discussion ou une pull request. Deux voies privées :

- le bouton « Report a vulnerability » de l'onglet **Security** de ce dépôt (signalement privé de
  GitHub) ;
- un courriel à **security@datacommune.fr**.

Décrivez ce qui est touché (site, API, script), comment reproduire, et l'effet constaté. Le
mainteneur accuse réception sous sept jours, tient la personne informée de la correction et la
remercie dans la note de version si elle le souhaite.

## Périmètre

- le site <https://datacommune.fr> et l'API <https://api.datacommune.fr> ;
- le code de ce dépôt, y compris les scripts de déploiement d'`infra/`.

Hors périmètre : les tests de charge ou de déni de service contre le site en ligne, l'ingénierie
sociale, et les données des producteurs publics (à signaler à leur producteur).

## Versions suivies

Seule la version servie sur <https://datacommune.fr> (la tête de `master`) reçoit des correctifs.

Voir aussi `/.well-known/security.txt` sur le site.
