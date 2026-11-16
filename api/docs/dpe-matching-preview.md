# Comparaison factuelle de diagnostics DPE

**58.3 — incrément non attributif, préparé localement.** Cette page décrit le contrat candidat ; elle n'atteste pas sa disponibilité en préproduction ou en production.

`POST /v1/bien/dpe/comparaison` compare les caractéristiques facultatives d'une cible à **chaque document de la page d'inventaire**. L'authentification et le quota sont ceux des routes DPE existantes. Aucune migration n'est requise.

Obtenir d'abord le `context_ref` de l'inventaire issu de la résolution de l'adresse. Le contexte, le curseur et les générations sont vérifiés par le service d'inventaire 58.2. Le corps JSON est fermé :

```json
{
  "contexte": "<context_ref opaque de l'inventaire>",
  "limite": 20,
  "cible": {
    "type_bien": "maison",
    "surface": {"value": 80, "nature": "habitable_dpe"}
  }
}
```

`cible` peut être vide ou omise. `type_bien` accepte `maison` ou `appartement`. Une surface exige un nombre positif fini et sa nature : `habitable_dpe`, `carrez`, `annonce`, `dvf` ou `autre`. Seule une surface déclarée `habitable_dpe` donne un écart arithmétique avec la surface DPE. Il n'existe aucun seuil de correspondance. Une référence ADEME, un étage, une porte, un bâtiment ou une adresse brute ne sont pas acceptés dans cette API.

Pour la suite de l'inventaire, renvoyer le même contexte et les critères, avec `curseur` égal à `inventory.next_cursor`. `limite` vaut 20 par défaut et reste entre 1 et 50. Les critères passent dans le corps POST ; ne pas les ajouter à une URL ni les stocker durablement.

Le résultat conserve `inventory` et sa pagination, ajoute `comparisons` pour les documents de cette page, les limites de la comparaison et `rule_version`. Chaque critère distingue donnée absente, non applicable, non comparable et observation disponible. Les blocages de cycle de vie ou de qualité sont explicites. Aucun document n'est supprimé parce qu'un attribut manque ou diffère. Les autres pages ne sont pas réputées comparées.

`attribution` conserve le contrat V2 : documents connus = `unassigned`, absence confirmée = `absent`, indisponibilité = `error`. La surface égale, l'unicité, le type et la référence documentaire ne permettent jamais de retourner `exact`. Un document consulté reste indépendant de la synthèse du logement et de ses exports.

Les réponses, y compris les erreurs, portent `Cache-Control: no-store`. Référence invalide : 400 ; contexte/génération à rafraîchir : 409 ; paramètres invalides : 422 ; indisponibilité : 503. Les erreurs publiques ne reproduisent pas les valeurs reçues. Authentification et quota conservent leurs codes existants.

## Limites et suite

L'association documentaire repose encore sur l'égalité BAN source, avec contrôle d'adresse déclaré non vérifié. Les données sources gelées servent à mesurer la disponibilité des critères ; elles ne prouvent pas l'identité d'un logement. La calibration positive, les critères privés, la date cible et l'adoption de la présentation restent dans les lots suivants de 58.3. DPE-MATCH-01/02 et BUG-01 restent ouverts. Une préproduction isolée et coordonnée doit qualifier le candidat avant toute promotion soumise à validation utilisateur.
