"""Registry of the report themes: one file per theme, discovered (04.7-01).

Every module of :mod:`app.rapport.themes` exports ``THEME``, a
:class:`~app.rapport.socle.DefinitionTheme`. Adding a theme is adding a file:
no list to edit, so plans working in parallel never touch the same file.
The order of the report is the section (``SECTIONS``), then ``ordre``, then
the id; two modules declaring one id fail the import.
"""

import importlib
import pkgutil
from types import ModuleType

from app.rapport import themes as paquet_themes
from app.rapport.schemas import SECTIONS
from app.rapport.socle import DefinitionTheme


def charger_themes(paquet: ModuleType = paquet_themes) -> tuple[DefinitionTheme, ...]:
    """The ``THEME`` of every module of *paquet*, in the order of the report."""
    definitions: dict[str, DefinitionTheme] = {}
    for module in sorted(pkgutil.iter_modules(paquet.__path__), key=lambda m: m.name):
        definition = getattr(
            importlib.import_module(f"{paquet.__name__}.{module.name}"), "THEME", None
        )
        if not isinstance(definition, DefinitionTheme):
            raise TypeError(f"{module.name} exports no THEME")
        if definition.id in definitions:
            raise ValueError(f"theme id {definition.id} declared twice")
        definitions[definition.id] = definition
    return tuple(
        sorted(
            definitions.values(),
            key=lambda d: (SECTIONS.index(d.section), d.ordre, d.id),
        )
    )


THEMES: tuple[DefinitionTheme, ...] = charger_themes()

# Catalogue keys the route declares (DATA-03): the identity (BAN, cadastre)
# and every source of every theme.
CLES_RAPPORT: tuple[str, ...] = tuple(
    dict.fromkeys(("ban", "cadastre", *(cle for d in THEMES for cle in d.sources)))
)
