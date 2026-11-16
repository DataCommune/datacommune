# Vérification du cadrage et du plan API

Contrôles terminés localement le 11 septembre 2026. Ce reçu valide les documents préparatoires, pas l'exécution du plan 63-01 ni la phase 63.

| Contrôle | Résultat |
|---|---|
| Structure GSD de 63-01 | Valide, trois tâches, aucune erreur ni avertissement |
| Organisation | Une phase active, un plan préparé, aucun résumé d'exécution ; huit exigences ouvertes |
| Conservation | Empreintes des 18 instantanés, 1 313 fichiers déplacés et 15 fichiers hors périmètre contrôlées |
| Preuves de l'audit précédent | 25 empreintes ; 16 recherches, 32 lectures ADEME, 157 projections sur cinq champs ; quatre consultations et 114 références distinctes sur six pages |
| Sources relues pour ce contrat | R06 et R04 : HTTP 200, une référence exacte chacune ; six champs comparés aux captures précédentes |
| Schémas interactifs | Sept parcours, deux états : 14 vues contrôlées sur ordinateur et à 375 px, aucune étiquette coupée ni débordement ; deux vues inspectées par capture |
| Revue du plan | Revue indépendante favorable après corrections ; les commandes PostGIS et les workflows existants sont réutilisés |

Résultats calculés et empreintes des livrables : [verification.json](verification.json). Réponses ADEME datées : [source-references.json](source-references.json). Leurs empreintes de réponse brute sont historiques : les octets bruts n'ont pas été conservés séparément ; le présent contrôle porte sur le fichier JSON conservé et ses valeurs.

La base de code examinée est le commit `3394395a32e9a778c89930ad7544406da265163e`, dans un checkout de référence resté intact. L'ancien audit décrit une préproduction datée ; aucune nouvelle vérification du déploiement DataCommune n'est revendiquée ici.

**Limites :** aucun code V3 implémenté, aucun test applicatif V3 exécuté, aucune CI du futur candidat, aucun import et aucun déploiement. La revue de conception UI ne constitue pas une acceptation humaine. Les schémas sont une explication du système et de sa cible, pas une validation de l'interface du site. La concordance d'un document avec l'ADEME ne prouve pas à elle seule son attribution à un logement physique.

Suite : exécuter 63-01 (suivi interne : `63-01-PLAN.md`), en commençant par les tests qui doivent échouer avant la modification.
