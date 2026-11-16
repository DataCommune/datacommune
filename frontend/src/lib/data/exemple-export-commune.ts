// Extracts of real answers of the open JSON export (`/commune/{code}.json`), read on datacommune.fr on
// 2026-11-11 and shortened: same keys, same values, the cuts written in the text (so an extract is
// not valid JSON). One line differs from what was read: `licence` is the sentence this code serves
// (`commune/[code].json/+server.ts`, reworded the same day; the test holds the two together).
// The developer page shows the extract of the commune of its example; there is one extract per
// example the URL models have had, so a change of example never shows another commune's answer.
// To refresh: read the export again and keep the same keys.
export const EXTRAITS_EXPORT_COMMUNE: Readonly<Record<string, string>> = {
	'44109': `{
  "format": "datacommune.commune.v1",
  "code_insee": "44109",
  "page": "https://datacommune.fr/commune/44109",
  "lu_le": "2026-11-11T15:35:35.010Z",
  "licence": "Chaque valeur reste sous la licence de son producteur, nommée dans « sources » ; une réutilisation cite la source. Mentionner DataCommune, avec le lien de la page, est bienvenu sans être une condition.",
  "etat": "fiche",
  "sources": [
    {
      "nom": "Demandes de valeurs foncières (DVF)",
      "producteur": "DGFiP / Etalab",
      "licence": "Licence Ouverte 2.0",
      "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees",
      "derniere_importation_reussie": "2026-10-31T21:22:59.686851Z"
    }
    … 12 autres sources
  ],
  "donnees": {
    "nom_commune": "Nantes",
    "population": 325070,
    "annee_demographie": 2022,
    "prix_m2_median_appartement": 3385,
    "nb_mutations_12m_appartement": 3996,
    "fenetre_mediane_dvf": "2024-12-31 → 2025-12-31",
    "loyer_m2_appartement": 14.5,
    "annee_loyers": 2025,
    "dpe_passoires_pct": 5.1
    … 45 autres champs
  }
}`,
	'31555': `{
  "format": "datacommune.commune.v1",
  "code_insee": "31555",
  "page": "https://datacommune.fr/commune/31555",
  "lu_le": "2026-11-11T15:49:30.275Z",
  "licence": "Chaque valeur reste sous la licence de son producteur, nommée dans « sources » ; une réutilisation cite la source. Mentionner DataCommune, avec le lien de la page, est bienvenu sans être une condition.",
  "etat": "fiche",
  "sources": [
    {
      "nom": "Demandes de valeurs foncières (DVF)",
      "producteur": "DGFiP / Etalab",
      "licence": "Licence Ouverte 2.0",
      "url": "https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees",
      "derniere_importation_reussie": "2026-10-31T21:22:59.686851Z"
    }
    … 12 autres sources
  ],
  "donnees": {
    "nom_commune": "Toulouse",
    "population": 511684,
    "annee_demographie": 2022,
    "prix_m2_median_appartement": 3250,
    "nb_mutations_12m_appartement": 6788,
    "fenetre_mediane_dvf": "2024-12-31 → 2025-12-31",
    "loyer_m2_appartement": 13.98,
    "annee_loyers": 2025,
    "dpe_passoires_pct": 3.9
    … 45 autres champs
  }
}`
};
