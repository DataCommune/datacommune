# Fonts of the per-fiche link previews (`/og/commune/{code}.png`, `/og/departement/{code}.png`).
# resvg reads TrueType, not WOFF2: this script decodes the site's own WOFF2 files (static/fonts, same
# subsets, same charter) into static TrueType instances, and writes the advance widths of every mapped
# character so that the server sizes a long name without rendering it twice.
# Run by hand when a font of static/fonts changes, from frontend/:
#   uv run --no-project --with fonttools --with brotli python scripts/og-polices.py
import json
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ICI = Path(__file__).resolve().parent
SOURCES = ICI.parent / "static" / "fonts"
CIBLE = ICI.parent / "src" / "lib" / "server" / "og" / "polices"

# (output name, WOFF2 sources merged for the metrics, weight)
INSTANCES = [
    ("inter-400", "Inter", ["inter-latin", "inter-latin-ext"], 400),
    ("inter-600", "Inter", ["inter-latin", "inter-latin-ext"], 600),
    ("newsreader-500", "Newsreader", ["newsreader-latin"], 500),
]


def nommer(police: TTFont, famille: str, poids: int) -> None:
    """One plain family name and the weight class: what resvg matches `font-family` and `font-weight` on."""
    noms = police["name"]
    for ident in (1, 16):
        noms.setName(famille, ident, 3, 1, 0x409)
    for ident in (2, 17):
        noms.setName("Regular", ident, 3, 1, 0x409)
    police["OS/2"].usWeightClass = poids


def main() -> None:
    CIBLE.mkdir(parents=True, exist_ok=True)
    metriques = {}
    for nom, famille, sources, poids in INSTANCES:
        avances = {}
        for index, source in enumerate(sources):
            police = TTFont(SOURCES / f"{source}.woff2")
            police = instantiateVariableFont(police, {"wght": poids}, updateFontNames=False)
            police.flavor = None
            nommer(police, famille, poids)
            suffixe = "" if index == 0 else "-ext"
            police.save(CIBLE / f"{nom}{suffixe}.ttf")
            unites = police["head"].unitsPerEm
            hmtx = police["hmtx"]
            for code, glyphe in police.getBestCmap().items():
                # The first source wins, as resvg's fallback takes the first font that has the glyph.
                avances.setdefault(str(code), round(hmtx[glyphe][0] / unites, 4))
        metriques[nom] = avances
    (CIBLE / "metriques.json").write_text(json.dumps(metriques, separators=(",", ":"), sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
