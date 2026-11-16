"""Validate monuments historiques against data.culture.gouv.fr."""

from __future__ import annotations

import httpx

from ..base import BaseValidator, FieldSpec
from ..compare import FieldType

# Retired source: this Opendatasoft export redirects (301) to the portal home page
# since 2026-09; the ETL reads Merimee from data.gouv.fr (etl_monuments/config.py).
API_BASE = (
    "https://data.culture.gouv.fr/api/explore/v2.1/catalog/datasets"
    "/liste-des-immeubles-proteges-au-titre-des-monuments-historiques/records"
)


class MonumentsValidator(BaseValidator):
    dataset_name = "monuments_historiques"
    table_name = "monuments_historiques"
    id_column = "reference"

    @property
    def fields_to_compare(self) -> list[FieldSpec]:
        return [
            FieldSpec("denomination", "appellation_courante", FieldType.TEXT_NORM),
            FieldSpec("commune", "commune", FieldType.TEXT_NORM),
            FieldSpec("code_insee", "code_insee", FieldType.EXACT),
            FieldSpec("protection", "type_de_protection", FieldType.TEXT_NORM),
            FieldSpec("latitude", "latitude", FieldType.COORD, tolerance=0.0001),
            FieldSpec("longitude", "longitude", FieldType.COORD, tolerance=0.0001),
        ]

    def fetch_gov_record(self, client: httpx.Client, row: dict) -> dict | None:
        ref = row["reference"]
        resp = client.get(
            API_BASE,
            params={"where": f'reference="{ref}"', "limit": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None
        return results[0]
