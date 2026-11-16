"""Pydantic schemas for Fiscalite locale tax rate endpoints.

Source: DGFiP — "Fiscalite locale des particuliers" (NOT the raw REI: the
chosen dataset ships pre-aggregated global rates, see app/etl_fiscalite/config.py).

D-01 (Phase 43, semantic contract): tax rates are stored and exposed in
percentage points on the whole chain — never as a 0-1 ratio. See the
`fiscalite.taux_*` entries in `contracts/fiche-fields.v1.json`.

D6 (truth of claims): this response exposes rates only. The imposition base
(`base_tfpb`) and the collected product (`produit_tfpb`) exist as columns in
`fiscalite_locale` but the source carries neither, so the ETL writes them
empty on every run — they are therefore NOT part of the public surface.
"""

from pydantic import BaseModel, ConfigDict


class ResultatFiscalite(BaseModel):
    """Local tax rates for a commune."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "75056",
                    "nom_commune": "PARIS",
                    "code_departement": "75",
                    "nom_epci": "Metropole du Grand Paris",
                    "taux_tfpb": 13.63,
                    "taux_tfpnb": 3.47,
                    "taux_th_rs": 13.63,
                    "taux_teom": 6.18,
                    "annee": 2024,
                }
            ]
        }
    )

    code_insee: str
    nom_commune: str | None = None
    code_departement: str | None = None
    nom_departement: str | None = None
    nom_region: str | None = None
    nom_epci: str | None = None
    taux_tfpb: float | None = None
    taux_tfpnb: float | None = None
    taux_th_rs: float | None = None
    taux_teom: float | None = None
    annee: int
