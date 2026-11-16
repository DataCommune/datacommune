"""Freshness verdicts are independent from notification delivery."""

from typing import Literal

from pydantic import BaseModel


class FreshnessMonitoringError(RuntimeError):
    """The data freshness verdict could not be computed."""


class FreshnessReport(BaseModel):
    status: Literal["healthy", "degraded"]
    checked: int
    alerts: list[str]
    notification: Literal["not_needed", "not_configured", "sent", "failed"]

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "degraded" else 0
