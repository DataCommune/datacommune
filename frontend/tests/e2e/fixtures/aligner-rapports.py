"""Give the mock-up reports the fields the API serves since 04.7-17.

`rapports-maquette.json` was built before the API served the headline, the
alert, the attachment sentence, the full summary and the map. This script
computes them with the API's own code (`app.rapport.alignement`,
`app.rapport.resume`, `app.rapport.carte`), so that the front reads served
values in its end-to-end tests and no rule of the API is copied in
TypeScript. The historical mock-up keeps its key figures; the full 43-theme
fixture uses the current summary of the API.

Run from the repository root, with the backend environment:

    api/backend/.venv/Scripts/python.exe frontend/tests/e2e/fixtures/aligner-rapports.py

It rewrites the fixture in place; running it twice changes nothing.

04.8-03 (D-35): with `--rapport-43`, it also writes `rapport-43-themes.json`, a served report holding
one theme per entry of the registry (43 today), for the full rendering of the fiche: the 16 themes
served for 2 rue des Essais in the mock-up, the served examples of the contract
(`contracts/rapport/exemples/*.json`) for the themes it lacks, and, for the themes neither has, a
theme without answer built by the API itself (`socle.sans_reponse`, states `manque`,
`hors_couverture` and `panne` in turn, verdicts of the API). The report is validated by
`RapportAdresse`, its summary and attachment sentence computed by the API code.

    api/backend/.venv/Scripts/python.exe frontend/tests/e2e/fixtures/aligner-rapports.py --rapport-43
"""

import json
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE / "api" / "backend"))
# Pure functions only: no database, no secret. `DEBUG` spares the production
# settings check, which wants secrets this script never reads.
os.environ.setdefault("DEBUG", "true")

from app.adresse.relations import PointBan  # noqa: E402
from app.rapport.alignement import aligner, phrase_rattachement  # noqa: E402
from app.rapport.carte import carte_de  # noqa: E402
from app.rapport.resume import resumer  # noqa: E402
from app.rapport.schemas import Theme  # noqa: E402

FIXTURE = Path(__file__).with_name("rapports-maquette.json")
FIXTURE_43 = Path(__file__).with_name("rapport-43-themes.json")
EXEMPLES = RACINE / "contracts" / "rapport" / "exemples"
BASE_43 = "44109_zz01_00002"
# BAN points of the three addresses (public, read on 2026-10-30 with the mock-up).
POINTS = {
    "44109_zz01_00002": PointBan(lon=-1.551375, lat=47.212028),
    "44109_zz02_00031": PointBan(lon=-1.537705, lat=47.194475),
    "85128_zz03_00004": PointBan(lon=-1.147068, lat=46.443909),
}


def aligner_rapport(ban_id: str, donnees: dict, *, preserve_key_figures: bool = True) -> dict:
    themes = [aligner(Theme.model_validate(t)) for t in donnees["themes"]]
    resume = resumer(themes).model_dump(mode="json")
    if preserve_key_figures:
        resume["chiffres_cles"] = donnees["resume"]["chiffres_cles"]
    carte = carte_de(ban_id, POINTS[ban_id])
    return {
        **donnees,
        "resume": resume,
        "themes": [t.model_dump(mode="json") for t in themes],
        "rattachement_phrase": phrase_rattachement(themes),
        "carte": carte.model_dump(mode="json") if carte else None,
    }


def rapport_43(adresses: list[dict]) -> dict:
    """A served report with one theme per registry entry, ordered by the registry."""
    from app.rapport.schemas import RapportAdresse
    from app.rapport.socle import sans_reponse
    from app.rapport.registre import THEMES

    base = next(a for a in adresses if a["banId"] == BASE_43)
    donnees = base["rapport"]["donnees"]
    servis = {t["id"]: t for t in donnees["themes"]}
    # One served example per theme id (the first file in name order, the plain variant before its variants).
    exemples: dict[str, dict] = {}
    for f in sorted(EXEMPLES.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        exemples.setdefault(t["id"], t)
    etats = ("manque", "hors_couverture", "panne")
    themes, construits = [], 0
    for definition in THEMES:
        brut = servis.get(definition.id) or exemples.get(definition.id)
        if brut is None:
            brut = sans_reponse(definition, etats[construits % len(etats)]).model_dump(mode="json")
            construits += 1
        themes.append(brut)
    rapport = aligner_rapport(BASE_43, {**donnees, "themes": themes}, preserve_key_figures=False)
    RapportAdresse.model_validate(rapport)
    return {**base, "slug": "rapport-43-themes", "rapport": {**base["rapport"], "donnees": rapport}}


def main() -> None:
    adresses = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if "--rapport-43" in sys.argv:
        FIXTURE_43.write_text(
            json.dumps(rapport_43(adresses), ensure_ascii=False, indent="\t") + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return
    for a in adresses:
        a["rapport"]["donnees"] = aligner_rapport(a["banId"], a["rapport"]["donnees"])
    FIXTURE.write_text(
        json.dumps(adresses, ensure_ascii=False, indent="\t") + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
