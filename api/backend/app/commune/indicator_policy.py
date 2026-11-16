"""Independent immutable runtime pins for the preproduction indicator reader."""

from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from app.commune.indicator_context import CogInput, IndicatorTrustInputs
from app.commune.indicator_manifest import (
    Closed,
    Instant,
    ManifestRefused,
    QualificationContext,
    parse_document,
)


class RuntimeIndicatorPolicy(Closed):
    scope: Literal["preproduction"]
    cog: CogInput
    evidence_root: Path
    checked_at: Instant
    dpe: QualificationContext
    dvf: QualificationContext

    @model_validator(mode="after")
    def coherent_pins(self) -> Self:
        for dataset, pins in (("dpe", self.dpe), ("dvf", self.dvf)):
            if (
                pins.dataset_name != dataset
                or pins.scope != self.scope
                or pins.cog_sha256 != self.cog.sha256
                or pins.cog_version != self.cog.version
                or pins.checked_at > self.checked_at
            ):
                raise ValueError("Runtime pins do not describe one reviewed context")
        return self

    @classmethod
    def from_json(cls, raw: str) -> "RuntimeIndicatorPolicy":
        try:
            if not raw:
                raise ValueError("Missing policy")
            value = parse_document(raw.encode("utf-8"))
            return cls.model_validate(value)
        except (ManifestRefused, ValueError, TypeError, UnicodeError):
            raise ManifestRefused("runtime_policy_invalid") from None

    def assert_runtime_environment(self, environment: str) -> None:
        if environment != "preproduction":
            raise ManifestRefused("runtime_environment_refused")

    def trust_inputs(self) -> IndicatorTrustInputs:
        return IndicatorTrustInputs(
            cog=self.cog,
            evidence_root=self.evidence_root,
            scope=self.scope,
            checked_at=self.checked_at,
            dpe=self.dpe,
            dvf=self.dvf,
        )
