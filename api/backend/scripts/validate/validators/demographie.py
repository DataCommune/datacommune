"""Validate demographie data against geo.api.gouv.fr."""

from __future__ import annotations

import httpx

from ..base import BaseValidator, FieldSpec
from ..compare import FieldType


class DemographieValidator(BaseValidator):
    dataset_name = "demographie"
    table_name = "demographie"
    id_column = "code_insee"

    @property
    def fields_to_compare(self) -> list[FieldSpec]:
        return [
            FieldSpec("nom_commune", "nom", FieldType.TEXT_NORM),
            FieldSpec("population", "population", FieldType.NUMERIC, tolerance=0.05),
        ]

    def fetch_gov_record(self, client: httpx.Client, row: dict) -> dict | None:
        code = row["code_insee"]
        resp = client.get(
            f"https://geo.api.gouv.fr/communes/{code}",
            params={"fields": "nom,population"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
