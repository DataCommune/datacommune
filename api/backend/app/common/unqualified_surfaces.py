"""Closed publication policy for unqualified derived surfaces (COR-09/13/15).

Reactivation requires reviewed source/grain/identity evidence, not an env flag.
"""

from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field

Surface = Literal["scoring_renov", "encadrement_loyers"]
REASONS: dict[Surface, str] = {
    "scoring_renov": (
        "Scoring rénovation indisponible : les rapprochements DPE, parcelles "
        "et ventes, leur couverture et leurs générations ne sont pas qualifiés. "
        "Aucun prix ni cible de logement n'est diffusé. "
        "Cette indisponibilité ne signifie pas absence de biens à rénover."
    ),
    "encadrement_loyers": (
        "Encadrement des loyers indisponible : la proximité d'un centroïde "
        "ne prouve pas l'appartenance à un quartier réglementaire. "
        "Aucun montant applicable à une adresse n'est diffusé. "
        "Cette indisponibilité ne signifie pas absence d'encadrement."
    ),
}


class SurfaceIndisponible(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statut: Literal["indisponible"] = "indisponible"
    surface: Surface
    raison: str
    donnees: list[dict] = Field(default_factory=list, max_length=0)


def unavailable(surface: Surface) -> SurfaceIndisponible:
    return SurfaceIndisponible(surface=surface, raison=REASONS[surface])


class SurfaceQuarantinedError(RuntimeError):
    """Permanent local policy refusal, before any SQL or publication effect."""

    def __init__(self, surface: Surface):
        self.surface = surface
        super().__init__(REASONS[surface])


def refuse(surface: Surface) -> NoReturn:
    raise SurfaceQuarantinedError(surface)
