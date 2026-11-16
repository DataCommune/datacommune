"""Validator registry."""

from .demographie import DemographieValidator
from .dpe import DpeValidator
from .monuments import MonumentsValidator

VALIDATORS: dict[str, type] = {
    "demographie": DemographieValidator,
    "monuments": MonumentsValidator,
    "dpe": DpeValidator,
}
