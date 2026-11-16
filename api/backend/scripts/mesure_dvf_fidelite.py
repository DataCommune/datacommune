#!/usr/bin/env python3
"""DVF fidelity measured from the source archive (plan 04.4-03).

Read-only: writes no file, prints on standard output. The JSON summary
(``archive``) holds aggregates and SHA-256 only, never a mutation id, an
address or a parcel (T-04.4-09); the per-mutation output (``empreintes``)
stays out of the repository.

Modes:

- ``archive``: reads the CSV files of an archived DVF run (each object checked
  against the SHA-256 of its record), groups the lines by mutation
  (``code_departement`` derived from ``code_commune`` as
  ``transform._derive_code_departement``, ``id_mutation``) and prints:

  - ``controle_2026_10_19``: the logic of the annex of 2026-10-19
    (annex mesure_dvf.py of the internal review of 2026-10-19)
    reproduced as it was: one local per line, geocoded lines against all
    lines. On run 48 it must give back the counts of that day, else the
    method is wrong;
  - ``regles_04_4``: the reference of each mutation under D-01 to D-04 (every
    line, locals by ``compte_locaux`` under D-02 amended by variant V, the
    non-local fields of ``reconstruction._AGGREGATION_SQL``, the address of
    the first line of ``min(code_commune)``, a position if one line has
    coordinates), the fate of each set of before under it, the effect of the
    deduplication alone (``dedup_seul``) and what V reclassifies against
    D-02 alone (``reclasse_par_v``, an expected effect, not a gap).

- ``empreintes``: one CSV line per mutation, fields of ``EMPREINTE_COLONNES``
  in that order then the set labels, NULL written ``\\N``, the address reduced
  to the SHA-256 of ``nom_commune|adresse_numero|adresse_suffixe|
  adresse_nom_voie|code_postal``.
- ``sql-base``: prints ``EMPREINTE_SQL`` (SELECT on ``mutations_grain``, the
  same encoding) wrapped in ``COPY (...) TO STDOUT WITH (FORMAT csv)`` for
  the work base's ``wb-psql.sh``.
- ``compare``: reads the fingerprints of the base and of the reference and
  the summary; prints ``ecarts`` (per field, missing or extra mutations) and
  ``ensembles`` (each set of before, read in the base under the keys of
  ``regles_04_4``), at most twenty identifiers as examples.

The qualification functions come from ``app/etl_dvf/qualification.py``
concatenated before this script on the host (guard on ``globals()``), else
from the package. To stay concatenable the script has no ``from __future__``
and the ``archive`` and ``empreintes`` modes use the standard library only
(Python 3.13 of the host); the local modes import ``app`` and ``scripts``
when called::

    cat api/backend/app/etl_dvf/qualification.py \\
        api/backend/scripts/mesure_dvf_fidelite.py \\
      | ssh <hypervisor> python3 - archive --run 48-20261019T171140Z
"""

import argparse
import calendar
import csv
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

if "compte_locaux" not in globals():  # run as a module, not concatenated
    from app.etl_dvf.qualification import (
        CompteursMutation,
        compte_locaux,
        qualifie_mutation,
    )

ARCHIVE_DVF = "/mnt/usb-8tb/datacommune-sources/dvf"
NULL = "\\N"
MONO_RESIDENTIEL = frozenset({"residentiel_maison", "residentiel_appartement"})
NON_RESIDENTIEL = frozenset({"terrain_seul", "hors_perimetre", "commerciale"})
MAX_EXEMPLES = 20

# Sets of the 2026-10-19 measure and the sub-key that states each one under
# the reference (``regles_04_4``) and in the base (``compare``, plan 05).
ENSEMBLES = {
    "vente_residentielle_devenue_non_residentielle": "encore_non_residentielles",
    "prix_invente_local_distinct_perdu": "prix_encore_calcule",
    "local_repete_par_parcelle": "mono_local_avec_prix",
    "adresse_commune_differente": "encore_differentes",
    "sans_aucune_ligne_geocodee": "presentes_sans_position",
}

EMPREINTE_COLONNES = [
    "code_departement",
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "classe_mutation",
    "nb_locaux_maison",
    "nb_locaux_appartement",
    "nb_locaux_commercial",
    "nb_locaux_dependance",
    "nb_lignes",
    "nb_dispositions",
    "nb_communes",
    "nb_parcelles",
    "surface_bati_totale",
    "surface_terrain_totale",
    "nombre_pieces_totales",
    "mono_local",
    "est_vente",
    "hors_stats_vefa",
    "valeur_divergente",
    "prix_m2",
    "code_commune",
    "adresse_sha256",
    "position_connue",
]

# Same encoding as ``empreinte()``. The price is the served formula
# (``app.dvf.queries._PRIX_M2_SQL``, equality checked by a test).
EMPREINTE_SQL = """
SELECT
    mg.code_departement,
    mg.id_mutation,
    coalesce(mg.date_mutation::text, '\\N'),
    coalesce(mg.nature_mutation, '\\N'),
    coalesce(round(mg.valeur_fonciere, 2)::text, '\\N'),
    coalesce(mg.classe_mutation, '\\N'),
    coalesce(mg.nb_locaux_maison::text, '\\N'),
    coalesce(mg.nb_locaux_appartement::text, '\\N'),
    coalesce(mg.nb_locaux_commercial::text, '\\N'),
    coalesce(mg.nb_locaux_dependance::text, '\\N'),
    coalesce(mg.nb_lignes::text, '\\N'),
    coalesce(mg.nb_dispositions::text, '\\N'),
    coalesce(mg.nb_communes::text, '\\N'),
    coalesce(mg.nb_parcelles::text, '\\N'),
    coalesce(mg.surface_bati_totale::text, '\\N'),
    coalesce(mg.surface_terrain_totale::text, '\\N'),
    coalesce(mg.nombre_pieces_totales::text, '\\N'),
    coalesce(mg.mono_local::text, '\\N'),
    coalesce(mg.est_vente::text, '\\N'),
    coalesce(mg.hors_stats_vefa::text, '\\N'),
    coalesce(mg.valeur_divergente::text, '\\N'),
    coalesce((CASE
        WHEN mg.classe_mutation NOT IN (
            'residentiel_maison', 'residentiel_appartement'
        ) THEN NULL
        WHEN NOT (mg.est_vente OR mg.hors_stats_vefa) THEN NULL
        WHEN NOT mg.mono_local
             OR mg.surface_bati_totale IS NULL OR mg.surface_bati_totale <= 0
             OR mg.valeur_fonciere IS NULL OR mg.valeur_fonciere <= 0 THEN NULL
        ELSE ROUND(mg.valeur_fonciere / mg.surface_bati_totale)
    END)::text, '\\N'),
    coalesce(mg.code_commune, '\\N'),
    encode(sha256(convert_to(concat_ws('|',
        coalesce(mg.nom_commune, '\\N'),
        coalesce(mg.adresse_numero, '\\N'),
        coalesce(mg.adresse_suffixe, '\\N'),
        coalesce(mg.adresse_nom_voie, '\\N'),
        coalesce(mg.code_postal, '\\N')
    ), 'UTF8')), 'hex'),
    (mg.position IS NOT NULL)::text
FROM mutations_grain mg
ORDER BY mg.code_departement, mg.id_mutation COLLATE "C"
"""

# The commune KPIs of ``v_commune_dvf`` (``app.commune.matview_ddl``), read as
# they are served; the filters below mirror that view (checked by a test).
COMPTES_COMMUNES = (
    "nb_mutations_12m",
    "nb_mutations_12m_maison",
    "nb_mutations_12m_appartement",
)
MEDIANES_COMMUNES = (
    "prix_m2_median",
    "prix_m2_median_maison",
    "prix_m2_median_appartement",
)
SNAPSHOT_COMMUNES_SQL = """
SELECT code_insee, nb_mutations_12m, nb_mutations_12m_maison,
    nb_mutations_12m_appartement, prix_m2_median, prix_m2_median_maison,
    prix_m2_median_appartement
FROM v_commune_dvf
ORDER BY code_insee COLLATE "C"
"""
# ``app.dvf.couverture.CLASSES_STATISTIQUES``.
CLASSES_STATISTIQUES = ("residentiel_maison", "residentiel_appartement")

# The chain from the served state to the state after the reimport, one cause
# per step; a mutation entering or leaving a commune population is counted
# under the step that moves it.
CAUSES = (
    "reclassement_d01",  # every line of a served mutation, one local per line
    "dedoublonnage_d02",  # locals counted once (D-02 amended, variant V)
    "ajout_sans_position_d03",  # mutations without any geocoded line
    "fenetre",  # the 12-month window of the state after
)

SQL_AUTORISEES = (EMPREINTE_SQL, SNAPSHOT_COMMUNES_SQL)


# -- reading the archive --------------------------------------------------------


def _nombre(valeur, decimal=False):
    valeur = (valeur or "").strip().replace(",", ".")
    if not valeur:
        return None
    try:
        return Decimal(valeur) if decimal else int(float(valeur))
    except (ValueError, ArithmeticError):
        return None


def _departement(code):
    """``transform._derive_code_departement``: 97x on 3, else 2 characters."""
    if not code or len(code) < 2:
        return "00"
    return code[:3] if code.startswith("97") and len(code) >= 3 else code[:2]


def _ligne(row):
    """One source line; the fields of the annex parsed as the annex did."""
    lon = (row.get("longitude") or "").strip()
    lat = (row.get("latitude") or "").strip()
    return {
        "id_mutation": row["id_mutation"],
        "date_mutation": row.get("date_mutation") or None,
        "code_type_local": (row.get("code_type_local") or "").strip() or None,
        "numero_disposition": row.get("numero_disposition") or None,
        "code_commune": row.get("code_commune") or None,
        "id_parcelle": row.get("id_parcelle") or None,
        "code_nature_culture": row.get("code_nature_culture") or None,
        "surface_reelle_bati": _nombre(row.get("surface_reelle_bati")),
        "surface_terrain": _nombre(row.get("surface_terrain")),
        "nombre_pieces_principales": _nombre(row.get("nombre_pieces_principales")),
        "nature_mutation": row.get("nature_mutation") or None,
        "valeur_fonciere": _nombre(row.get("valeur_fonciere"), decimal=True),
        "nom_commune": row.get("nom_commune") or None,
        "adresse_numero": row.get("adresse_numero") or None,
        "adresse_suffixe": row.get("adresse_suffixe") or None,
        "adresse_nom_voie": row.get("adresse_nom_voie") or None,
        "code_postal": row.get("code_postal") or None,
        "geo": bool(lon) and bool(lat),
    }


def _record(racine, run):
    with open(f"{racine}/runs/{run}.json", encoding="utf-8") as handle:
        return json.load(handle)


def _mutations(racine, record):
    """Yield ``(key, lines)`` per mutation, lines in file order (the ``id`` order).

    Grouped one file at a time (a departement file holds its own mutations);
    the lines without commune code (departement ``00``) are grouped over all
    files, as in the base. A key met again in a later file breaks that
    assumption: the measure stops rather than count one mutation twice.
    """
    vues = set()
    sans_departement = defaultdict(list)
    for fichier in record["files"]:
        with open(f"{racine}/{fichier['object']}", "rb") as handle:
            brut = handle.read()
        if hashlib.sha256(brut).hexdigest() != fichier["sha256"]:
            raise SystemExit(f"sha256 differente de son record: {fichier['relpath']}")
        groupes = defaultdict(list)
        lecteur = csv.DictReader(io.StringIO(brut.decode("utf-8"), newline=""))
        del brut
        for row in lecteur:
            ligne = _ligne(row)
            cle = (_departement(row.get("code_commune") or ""), ligne["id_mutation"])
            (sans_departement if cle[0] == "00" else groupes)[cle].append(ligne)
        for cle, lignes in groupes.items():
            if cle in vues:
                raise SystemExit(
                    f"mutation sur plusieurs fichiers: {fichier['relpath']}"
                )
            vues.add(cle)
            yield cle, lignes
    yield from sans_departement.items()


# -- counters -----------------------------------------------------------------


def _est_local(ligne):
    """A local: type, surface or rooms present."""
    return (
        ligne["code_type_local"] is not None
        or ligne["surface_reelle_bati"] is not None
        or ligne["nombre_pieces_principales"] is not None
    )


def _est_comptee(ligne):
    """``reconstruction._LIGNE_LOCAL``: a local or a land surface (IN-05)."""
    return _est_local(ligne) or ligne["surface_terrain"] is not None


def _distincts(lignes, champ):
    return len({ligne[champ] for ligne in lignes if ligne[champ] is not None})


def _somme(valeurs):
    presentes = [v for v in valeurs if v is not None]
    return sum(presentes) if presentes else None


# How the locals of a mutation are counted: ``V`` is the rule of the base
# (D-02 amended, variant V, decision of Ianis on 2026-10-21); ``D-02`` is the
# same ``compte_locaux`` with the culture of every line set to NULL, so the
# multiplicity is the max over the parcels only (the D-02 of before the
# amendment, kept to name what V reclassifies); ``naif`` is one local per line.
REGLES_LOCAUX = ("V", "D-02", "naif")


def _compteurs(lignes, regle="V"):
    """Counters of ``reconstruction._AGGREGATION_SQL`` on *lignes*.

    Locals by ``compte_locaux`` under *regle* (``REGLES_LOCAUX``), or one
    local per line for ``naif`` (the SQL of before: ``count(*) FILTER``, sums
    of surface and rooms, and of the land surfaces). Land surface under V and
    D-02 by ``compte_locaux`` too (IN-05). Every other field is the SQL one:
    nature and amount by maximum, distinct non-null values.
    """
    if regle not in REGLES_LOCAUX:
        raise ValueError(f"regle inconnue: {regle}")
    natures = [ligne["nature_mutation"] for ligne in lignes if ligne["nature_mutation"]]
    valeurs = [
        ligne["valeur_fonciere"]
        for ligne in lignes
        if ligne["valeur_fonciere"] is not None
    ]
    locaux = [ligne for ligne in lignes if _est_local(ligne)]
    if regle != "naif":
        comptes = compte_locaux(
            (
                ligne["numero_disposition"],
                ligne["code_type_local"],
                ligne["surface_reelle_bati"],
                ligne["nombre_pieces_principales"],
                ligne["id_parcelle"],
                ligne["code_nature_culture"] if regle == "V" else None,
                ligne["surface_terrain"],
            )
            for ligne in lignes
            if _est_comptee(ligne)
        )
        par_type = {
            "1": comptes.nb_locaux_maison,
            "2": comptes.nb_locaux_appartement,
            "4": comptes.nb_locaux_commercial,
            "3": comptes.nb_locaux_dependance,
        }
        surface, pieces = comptes.surface_bati_totale, comptes.nombre_pieces_totales
        terrain = comptes.surface_terrain_totale
    else:
        par_type = Counter(ligne["code_type_local"] for ligne in locaux)
        surface = _somme(ligne["surface_reelle_bati"] for ligne in locaux)
        pieces = _somme(ligne["nombre_pieces_principales"] for ligne in locaux)
        terrain = _somme(ligne["surface_terrain"] for ligne in lignes)
    return CompteursMutation(
        nb_locaux_maison=par_type["1"],
        nb_locaux_appartement=par_type["2"],
        nb_locaux_commercial=par_type["4"],
        nb_locaux_dependance=par_type["3"],
        nb_lignes=len(lignes),
        nb_dispositions=_distincts(lignes, "numero_disposition"),
        nb_communes=_distincts(lignes, "code_commune"),
        nb_parcelles=_distincts(lignes, "id_parcelle"),
        surface_bati_totale=surface,
        surface_terrain_totale=terrain,
        nombre_pieces_totales=pieces,
        nature_mutation=max(natures) if natures else None,
        valeur_fonciere=max(valeurs) if valeurs else None,
        valeur_divergente=len(set(valeurs)) > 1,
    )


def _compte_naif(lignes):
    """``compte_lignes`` of 52bdb0ca (the annex): one local per line, the
    first filled nature, values other than zero; no log."""
    lignes = list(lignes)

    def distincts(champ):
        return len({ligne[champ] for ligne in lignes if ligne.get(champ)})

    par_type = Counter(ligne.get("code_type_local") for ligne in lignes)
    valeurs = {
        ligne.get("valeur_fonciere") for ligne in lignes if ligne.get("valeur_fonciere")
    }
    nature = next(
        (ligne["nature_mutation"] for ligne in lignes if ligne.get("nature_mutation")),
        None,
    )
    return CompteursMutation(
        nb_locaux_maison=par_type["1"],
        nb_locaux_appartement=par_type["2"],
        nb_locaux_commercial=par_type["4"],
        nb_locaux_dependance=par_type["3"],
        nb_lignes=len(lignes),
        nb_dispositions=distincts("numero_disposition"),
        nb_communes=distincts("code_commune"),
        nb_parcelles=distincts("id_parcelle"),
        surface_bati_totale=_somme(
            ligne.get("surface_reelle_bati") for ligne in lignes
        ),
        surface_terrain_totale=_somme(ligne.get("surface_terrain") for ligne in lignes),
        nombre_pieces_totales=_somme(
            ligne.get("nombre_pieces_principales") for ligne in lignes
        ),
        nature_mutation=nature,
        valeur_fonciere=max(valeurs) if valeurs else None,
        valeur_divergente=len(valeurs) > 1,
    )


def _ligne_adresse(lignes):
    """D-04: first line (file order) of ``min(code_commune)``, else the first."""
    communes = [ligne["code_commune"] for ligne in lignes if ligne["code_commune"]]
    if not communes:
        return lignes[0]
    retenue = min(communes)
    return next(ligne for ligne in lignes if ligne["code_commune"] == retenue)


def _sha_adresse(ligne):
    champs = (
        "nom_commune",
        "adresse_numero",
        "adresse_suffixe",
        "adresse_nom_voie",
        "code_postal",
    )
    texte = "|".join(NULL if ligne[c] is None else ligne[c] for c in champs)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _texte(valeur):
    if valeur is None:
        return NULL
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    return str(valeur)


def _fusion_maison(lignes):
    """A complete house signature on two (parcel, culture) pairs or more,
    merged by variant V."""
    par_signature = defaultdict(Counter)
    for ligne in lignes:
        if (
            ligne["code_type_local"] == "1"
            and ligne["surface_reelle_bati"] is not None
            and ligne["nombre_pieces_principales"] is not None
        ):
            signature = (
                ligne["numero_disposition"],
                ligne["surface_reelle_bati"],
                ligne["nombre_pieces_principales"],
            )
            paire = (ligne["id_parcelle"], ligne["code_nature_culture"])
            par_signature[signature][paire] += 1
    return any(
        sum(parcelles.values()) > max(parcelles.values())
        for parcelles in par_signature.values()
    )


# -- one mutation -------------------------------------------------------------


# One block, in the order of the annex, so the two read side by side.
def _controle(lignes, out, transitions):  # noqa: C901
    """The annex of 2026-10-19, as it was; returns the sets the mutation is in."""
    ensembles = set()
    geo_l = [ligne for ligne in lignes if ligne["geo"]]
    q_all = qualifie_mutation(_compte_naif(lignes))
    vente = q_all.est_vente or q_all.hors_stats_vefa
    out["mutations"] += 1
    if not geo_l:
        out["sans_aucune_ligne_geocodee"] += 1
        ensembles.add("sans_aucune_ligne_geocodee")
        return ensembles
    out["chargees"] += 1
    q_geo = qualifie_mutation(_compte_naif(geo_l))
    res_large = MONO_RESIDENTIEL | {"ensemble_residentiel", "mixte"}
    if len(geo_l) < len(lignes):
        out["partiellement_geocodees"] += 1
        if q_geo.classe_mutation != q_all.classe_mutation:
            out["A5b01_classe_changee"] += 1
            transitions[f"{q_all.classe_mutation} -> {q_geo.classe_mutation}"] += 1
            if q_geo.prix_m2 is not None and q_all.prix_m2 is None:
                out["A5b01_prix_m2_invente"] += 1

                def sig(ligne):
                    return (
                        ligne["code_type_local"],
                        ligne["surface_reelle_bati"],
                        ligne["nombre_pieces_principales"],
                    )

                types = ("1", "2", "4")
                kept = {sig(x) for x in geo_l if x["code_type_local"] in types}
                lost = [
                    x for x in lignes if not x["geo"] and x["code_type_local"] in types
                ]
                if lost and all(sig(x) in kept for x in lost):
                    out["A5b01_prix_invente_dont_local_repete_par_parcelle"] += 1
                    ensembles.add("local_repete_par_parcelle")
                else:
                    out["A5b01_prix_invente_dont_local_distinct_perdu"] += 1
                    ensembles.add("prix_invente_local_distinct_perdu")
        lost_res = [
            x for x in lignes if not x["geo"] and x["code_type_local"] in ("1", "2")
        ]
        if (
            lost_res
            and q_all.classe_mutation in res_large
            and q_geo.classe_mutation in NON_RESIDENTIEL
        ):
            out["A5b01_vente_residentielle_devenue_non_residentielle"] += 1
            ensembles.add("vente_residentielle_devenue_non_residentielle")
        if (
            q_geo.prix_m2 is not None
            and q_all.prix_m2 is not None
            and q_geo.prix_m2 != q_all.prix_m2
        ):
            out["A5b01_prix_m2_different"] += 1
        if (
            q_geo.classe_mutation in MONO_RESIDENTIEL
            and vente
            and q_geo.prix_m2 is not None
            and q_all.prix_m2 is None
        ):
            out["A5b01_vente_residentielle_mono_avec_prix_faux"] += 1
    communes = {x["code_commune"] for x in geo_l if x["code_commune"]}
    if len(communes) > 1:
        out["A5b02_multi_communes"] += 1
        if geo_l[0]["code_commune"] != min(communes):
            out["A5b02_adresse_et_commune_de_lignes_differentes"] += 1
            ensembles.add("adresse_commune_differente")
            if vente and q_geo.classe_mutation in res_large:
                out["A5b02_dont_ventes_residentielles"] += 1
    return ensembles


def empreinte(cle, lignes):
    """Reference of one mutation (D-01 to D-04) in ``EMPREINTE_COLONNES`` order.

    Also returns its counters and qualification, for the summary.
    """
    compteurs = _compteurs(lignes)
    q = qualifie_mutation(compteurs)
    adresse = _ligne_adresse(lignes)
    communes = [x["code_commune"] for x in lignes if x["code_commune"]]
    dates = [x["date_mutation"] for x in lignes if x["date_mutation"]]
    valeur = compteurs.valeur_fonciere
    prix = q.prix_m2
    ligne = [
        cle[0],
        cle[1],
        min(dates) if dates else None,
        compteurs.nature_mutation,
        None if valeur is None else valeur.quantize(Decimal("0.01"), ROUND_HALF_UP),
        q.classe_mutation,
        compteurs.nb_locaux_maison,
        compteurs.nb_locaux_appartement,
        compteurs.nb_locaux_commercial,
        compteurs.nb_locaux_dependance,
        compteurs.nb_lignes,
        compteurs.nb_dispositions,
        compteurs.nb_communes,
        compteurs.nb_parcelles,
        compteurs.surface_bati_totale,
        compteurs.surface_terrain_totale,
        compteurs.nombre_pieces_totales,
        q.mono_local,
        q.est_vente,
        q.hors_stats_vefa,
        compteurs.valeur_divergente,
        None if prix is None else prix.quantize(Decimal(1), ROUND_HALF_UP),
        min(communes) if communes else None,
        _sha_adresse(adresse),
        any(x["geo"] for x in lignes),
    ]
    return [_texte(v) for v in ligne], compteurs, q


# -- commune KPIs -------------------------------------------------------------


def debut_fenetre(date):
    """``date - INTERVAL '12 months'`` in PostgreSQL: the day bounded by the
    end of the month (2024-02-29 gives 2023-02-28)."""
    annee, mois, jour = (int(x) for x in date.split("-"))
    fin = calendar.monthrange(annee - 1, mois)[1]
    return f"{annee - 1:04d}-{mois:02d}-{min(jour, fin):02d}"


def _etat_vue(lignes, compteurs, q):
    """``(date, commune, class)`` of a mutation in ``v_commune_dvf``; the class
    is None when a filter other than the window excludes it."""
    dates = [x["date_mutation"] for x in lignes if x["date_mutation"]]
    communes = [x["code_commune"] for x in lignes if x["code_commune"]]
    retenue = (
        q.classe_mutation in CLASSES_STATISTIQUES
        and q.est_vente
        and not q.hors_stats_vefa
        and compteurs.nb_communes == 1
        and (compteurs.surface_bati_totale or 0) > 0
        and (compteurs.valeur_fonciere or 0) > 0
    )
    return (
        min(dates) if dates else None,
        min(communes) if communes else None,
        q.classe_mutation if retenue else None,
    )


class PredictionCommunes:
    """``v_commune_dvf`` predicted per commune, served and after the reimport.

    Each mutation is seen in three forms: its geocoded lines with one local
    per line (the state built by the code of before), all its lines with one
    local per line (D-01), all its lines under ``compte_locaux`` (V). The
    chain of states, one cause (``CAUSES``) per step, goes from the served
    population to the population after; the entries and exits of each step
    are counted per commune. Aggregates only: counts and the SHA-256 of each
    population (``departement|id_mutation|class``), never an identifier.
    """

    def __init__(self):
        self.date_max = {"servi": None, "apres": None}
        self.mutations = []

    def _date_max(self, etat, date):
        if date and (self.date_max[etat] is None or date > self.date_max[etat]):
            self.date_max[etat] = date

    def ajoute(self, cle, lignes, naif, apres):
        """*naif* and *apres*: ``(counters, qualification)`` on all the lines,
        one local per line and under V."""
        if cle[0] == "00":  # no commune: outside every hosted departement
            return
        geo = [x for x in lignes if x["geo"]]
        etat_apres = _etat_vue(lignes, *apres)
        self._date_max("apres", etat_apres[0])
        etat_geo = etat_naif = None
        if geo:
            etat_naif = _etat_vue(lignes, *naif)
            if len(geo) == len(lignes):
                etat_geo = etat_naif
            else:
                compteurs = _compteurs(geo, regle="naif")
                etat_geo = _etat_vue(geo, compteurs, qualifie_mutation(compteurs))
            self._date_max("servi", etat_geo[0])
        etats = (etat_geo, etat_naif, etat_apres)
        if any(e is not None and e[2] is not None for e in etats):
            self.mutations.append((cle, bool(geo), etats))

    def resultat(self):
        fenetres = {
            nom: {"date_max": d, "debut": d and debut_fenetre(d)}
            for nom, d in self.date_max.items()
        }

        def membre(etat, fenetre):
            debut = fenetres[fenetre]["debut"]
            if etat is None or etat[2] is None or not etat[0] or not debut:
                return None
            return (etat[1], etat[2]) if etat[0] >= debut else None

        # served, then one step per cause: D-01, D-02 (V), D-03, window.
        etapes = (
            lambda servie, e: membre(e[0], "servi"),
            lambda servie, e: membre(e[1], "servi"),
            lambda servie, e: membre(e[2], "servi") if servie else None,
            lambda servie, e: membre(e[2], "servi"),
            lambda servie, e: membre(e[2], "apres"),
        )
        populations = [defaultdict(set) for _ in etapes]
        for cle, servie, etats in self.mutations:
            for population, etape in zip(populations, etapes, strict=True):
                retenu = etape(servie, etats)
                if retenu:
                    population[retenu[0]].add((cle, retenu[1]))
        communes = sorted(set().union(*populations))
        return fenetres, {
            code: {
                "servi": _etat_commune(populations[0][code]),
                "apres": _etat_commune(populations[-1][code]),
                "causes": _causes(code, populations),
            }
            for code in communes
        }


def _etat_commune(population):
    classes = Counter(classe for _, classe in population)
    texte = "\n".join(sorted(f"{d}|{i}|{classe}" for (d, i), classe in population))
    return {
        "nb_mutations_12m": len(population),
        "nb_mutations_12m_maison": classes["residentiel_maison"],
        "nb_mutations_12m_appartement": classes["residentiel_appartement"],
        "population_sha256": hashlib.sha256(texte.encode("utf-8")).hexdigest(),
    }


def _causes(code, populations):
    causes = {}
    for cause, avant, apres in zip(
        CAUSES, populations[:-1], populations[1:], strict=True
    ):
        entrees = len(apres[code] - avant[code])
        sorties = len(avant[code] - apres[code])
        if entrees or sorties:
            causes[cause] = {"entrees": entrees, "sorties": sorties}
    return causes


# -- modes ----------------------------------------------------------------------


def _sha_liste(cles):
    texte = "\n".join(sorted(f"{d}|{i}" for d, i in cles))
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _sort_sous_reference(nom, q, ligne_empreinte, lignes):
    """Whether a member of set *nom* still shows its defect under the reference."""
    if nom == "vente_residentielle_devenue_non_residentielle":
        return q.classe_mutation in NON_RESIDENTIEL
    if nom == "prix_invente_local_distinct_perdu":
        return q.prix_m2 is not None
    if nom == "local_repete_par_parcelle":
        return q.classe_mutation in MONO_RESIDENTIEL and q.prix_m2 is not None
    if nom == "adresse_commune_differente":
        code = ligne_empreinte[EMPREINTE_COLONNES.index("code_commune")]
        return _texte(_ligne_adresse(lignes)["code_commune"]) != code
    return ligne_empreinte[-1] == "false"  # sans_aucune_ligne_geocodee


def mode_archive(racine, run):
    record = _record(racine, run)
    out = Counter()
    transitions = Counter()
    rows_total = rows_geo = 0
    membres = {nom: [] for nom in ENSEMBLES}
    sous_reference = Counter()
    classes = Counter()
    dedup = {"compte": 0, "transitions": Counter(), "fusions": 0, "fusions_classe": 0}
    reclasse_v = {"compte": 0, "transitions": Counter()}
    faits = Counter()
    date_max = date_max_geo = None
    communes = PredictionCommunes()
    for cle, lignes in _mutations(racine, record):
        rows_total += len(lignes)
        rows_geo += sum(1 for x in lignes if x["geo"])
        for x in lignes:
            date = x["date_mutation"]
            if date and (date_max is None or date > date_max):
                date_max = date
            if date and x["geo"] and (date_max_geo is None or date > date_max_geo):
                date_max_geo = date
            faits["lignes_sans_code_commune"] += x["code_commune"] is None
            faits["lignes_locaux_sans_parcelle"] += (
                _est_local(x) and x["id_parcelle"] is None
            )
            faits["valeurs_ge_1e10"] += x["valeur_fonciere"] is not None and x[
                "valeur_fonciere"
            ] >= Decimal("1e10")
        ensembles = _controle(lignes, out, transitions)
        ligne, compteurs, q = empreinte(cle, lignes)
        classes[q.classe_mutation] += 1
        faits["multi_communes_toutes_lignes"] += compteurs.nb_communes > 1
        premiere = next(
            (x["nature_mutation"] for x in lignes if x["nature_mutation"]), None
        )
        faits["natures_divergentes"] += premiere != compteurs.nature_mutation
        for nom in ensembles:
            membres[nom].append(cle)
            sous_reference[nom] += _sort_sous_reference(nom, q, ligne, lignes)
        compteurs_naif = _compteurs(lignes, regle="naif")
        naif = qualifie_mutation(compteurs_naif)
        communes.ajoute(cle, lignes, (compteurs_naif, naif), (compteurs, q))
        d02 = qualifie_mutation(_compteurs(lignes, regle="D-02"))
        if d02.classe_mutation != q.classe_mutation:
            reclasse_v["compte"] += 1
            transition = f"{d02.classe_mutation} -> {q.classe_mutation}"
            reclasse_v["transitions"][transition] += 1
        fusion = _fusion_maison(lignes)
        dedup["fusions"] += fusion
        if naif.classe_mutation != q.classe_mutation:
            dedup["compte"] += 1
            dedup["transitions"][f"{naif.classe_mutation} -> {q.classe_mutation}"] += 1
            dedup["fusions_classe"] += fusion

    controle = {"rows_total": rows_total, "rows_geocoded": rows_geo, **out}
    controle["A5b01_transitions"] = dict(transitions.most_common())
    regles = {
        nom: {"total": len(membres[nom]), cle: sous_reference[nom]}
        for nom, cle in ENSEMBLES.items()
    }
    regles["dedup_seul"] = {
        "compte": dedup["compte"],
        "transitions": dict(dedup["transitions"].most_common()),
        "fusions_maison_signature_complete": dedup["fusions"],
        "fusions_maison_dont_classe_changee": dedup["fusions_classe"],
    }
    regles["reclasse_par_v"] = {
        "compte": reclasse_v["compte"],
        "transitions": dict(reclasse_v["transitions"].most_common()),
    }
    regles["classes"] = dict(sorted(classes.items()))
    for nom in (
        "natures_divergentes",
        "lignes_sans_code_commune",
        "lignes_locaux_sans_parcelle",
        "multi_communes_toutes_lignes",
        "valeurs_ge_1e10",
    ):
        regles[nom] = faits[nom]
    regles["date_max_toutes_lignes"] = date_max
    regles["date_max_lignes_geocodees"] = date_max_geo
    regles["ensembles_sha256"] = {nom: _sha_liste(membres[nom]) for nom in ENSEMBLES}
    fenetres, par_commune = communes.resultat()
    return {
        "run": run,
        "python": sys.version.split()[0],
        "fichiers": [
            {"relpath": f["relpath"], "bytes": f["bytes"], "sha256": f["sha256"]}
            for f in record["files"]
        ],
        "controle_2026_10_19": controle,
        "regles_04_4": regles,
        "fenetres_communes": fenetres,
        "communes": par_commune,
    }


def mode_empreintes(racine, run, sortie):
    writer = csv.writer(sortie, lineterminator="\n")
    record = _record(racine, run)
    out, transitions = Counter(), Counter()
    for cle, lignes in _mutations(racine, record):
        ensembles = _controle(lignes, out, transitions)
        ligne, _, _ = empreinte(cle, lignes)
        writer.writerow([*ligne, ";".join(sorted(ensembles)) or NULL])


def mode_sql_base(communes=False):
    """``EMPREINTE_SQL`` (or ``SNAPSHOT_COMMUNES_SQL``) as a ``COPY`` to CSV."""
    from scripts.verify_golden_cases import assert_select_only

    sql = SNAPSHOT_COMMUNES_SQL if communes else EMPREINTE_SQL
    assert_select_only(sql, SQL_AUTORISEES)
    requete = " ".join(sql.split())
    return f"COPY ({requete}) TO STDOUT WITH (FORMAT csv);"


def _lire_empreintes(chemin):
    with open(chemin, newline="", encoding="utf-8") as handle:
        return {(r[0], r[1]): r for r in csv.reader(handle) if r}


def _defaut_en_base(nom, base, reference):
    """The state of a member of set *nom* read in the base fingerprint."""
    col = EMPREINTE_COLONNES.index
    if nom == "vente_residentielle_devenue_non_residentielle":
        return base[col("classe_mutation")] in NON_RESIDENTIEL
    if nom == "prix_invente_local_distinct_perdu":
        return base[col("prix_m2")] != NULL
    if nom == "local_repete_par_parcelle":
        return base[col("classe_mutation")] in MONO_RESIDENTIEL and (
            base[col("prix_m2")] != NULL
        )
    if nom == "adresse_commune_differente":
        return base[col("adresse_sha256")] != reference[col("adresse_sha256")]
    return base[col("position_connue")] == "false"


def mode_compare(chemin_base, chemin_reference, chemin_resume):
    """Base fingerprints against the reference, field by field and per set."""
    base = _lire_empreintes(chemin_base)
    reference = _lire_empreintes(chemin_reference)
    with open(chemin_resume, encoding="utf-8") as handle:
        regles = json.load(handle)["regles_04_4"]
    champs = EMPREINTE_COLONNES[2:]
    ecarts = dict.fromkeys(champs, 0)
    exemples = []
    for cle, ref in reference.items():
        lue = base.get(cle)
        if lue is None:
            continue
        for i, champ in enumerate(champs, start=2):
            if lue[i] != ref[i]:
                ecarts[champ] += 1
                if len(exemples) < MAX_EXEMPLES:
                    exemples.append({"mutation": f"{cle[0]}|{cle[1]}", "champ": champ})
    manquantes = [cle for cle in reference if cle not in base]
    en_trop = [cle for cle in base if cle not in reference]
    ecarts["mutations_manquantes"] = len(manquantes)
    ecarts["mutations_en_trop"] = len(en_trop)
    exemples.extend(
        {"mutation": f"{cle[0]}|{cle[1]}", "champ": "presence"}
        for cle in (manquantes + en_trop)[: max(0, MAX_EXEMPLES - len(exemples))]
    )
    ensembles = {}
    for nom, sous_cle in ENSEMBLES.items():
        presents = defaut = 0
        for cle, ref in reference.items():
            if nom not in ref[-1].split(";") or cle not in base:
                continue
            presents += 1
            defaut += _defaut_en_base(nom, base[cle], ref)
        ensembles[nom] = {
            "total": presents,
            sous_cle: defaut,
            "attendu": regles.get(nom, {}).get(sous_cle),
        }
    return {"ecarts": ecarts, "ensembles": ensembles, "exemples": exemples}


def _lire_instantane(chemin):
    """A CSV of ``SNAPSHOT_COMMUNES_SQL`` (COPY csv, no header, NULL empty)."""
    colonnes = (*COMPTES_COMMUNES, *MEDIANES_COMMUNES)
    with open(chemin, newline="", encoding="utf-8") as handle:
        return {
            r[0]: dict(
                zip(colonnes, (int(v) if v else None for v in r[1:]), strict=True)
            )
            for r in csv.reader(handle)
            if r
        }


def _comptes(etats, code):
    etat = etats.get(code) or {}
    return tuple(etat.get(k) or 0 for k in COMPTES_COMMUNES)


def _medianes(etats, code):
    etat = etats.get(code) or {}
    return tuple(etat.get(k) for k in MEDIANES_COMMUNES)


def mode_kpi(chemin_avant, chemin_apres, chemin_reference, chemin_servi=None):
    """Commune snapshots of ``v_commune_dvf`` against the predictions.

    *chemin_avant* is judged against the state ``servi`` of the summary
    *chemin_servi* (default *chemin_reference*): 0 gap proves the predictor
    rebuilds the served base. With *chemin_apres*, the snapshot after is
    judged against the state ``apres`` of *chemin_reference*; each commune
    is explained by the causes of the chain, plus ``edition`` when the served
    state comes from another run (a net count per commune: the summaries
    carry no identifier). A count other than predicted, or a median moved
    while the predicted population did not, is unexplained.
    """
    with open(chemin_reference, encoding="utf-8") as handle:
        reference = json.load(handle)
    servi = reference
    if chemin_servi:
        with open(chemin_servi, encoding="utf-8") as handle:
            servi = json.load(handle)
    pred_servi = {c: v["servi"] for c, v in servi["communes"].items()}
    pred_apres = {c: v["apres"] for c, v in reference["communes"].items()}
    avant = _lire_instantane(chemin_avant)
    apres = _lire_instantane(chemin_apres) if chemin_apres else None
    codes = set(pred_servi) | set(avant)
    if apres is not None:
        codes |= set(pred_apres) | set(apres)
    codes = sorted(codes)
    inexpliques = []

    def ecarts(lu, predit, raison):
        communes = [c for c in codes if _comptes(lu, c) != _comptes(predit, c)]
        inexpliques.extend({"code_insee": c, "raison": raison} for c in communes)
        return len(communes)

    rapport = {
        "communes_comparees": len(codes),
        "ecarts_comptes_servi": ecarts(
            avant, pred_servi, "compte_servi_different_de_la_prediction"
        ),
    }
    if apres is None:
        rapport["inexpliques"] = inexpliques
        return rapport
    rapport["ecarts_comptes_apres"] = ecarts(
        apres, pred_apres, "compte_apres_different_de_la_prediction"
    )
    autre_edition = servi.get("run") != reference.get("run")
    causes = defaultdict(Counter)
    medianes_changees = 0
    for code in codes:
        commune = reference["communes"].get(code, {})
        for cause, effet in commune.get("causes", {}).items():
            causes[cause]["communes"] += 1
            causes[cause]["entrees"] += effet["entrees"]
            causes[cause]["sorties"] += effet["sorties"]
        sha_servi = (pred_servi.get(code) or {}).get("population_sha256")
        servi_reimport = commune.get("servi") or {}
        if autre_edition and sha_servi != servi_reimport.get("population_sha256"):
            net = _comptes({code: servi_reimport}, code)[0]
            net -= _comptes(pred_servi, code)[0]
            causes["edition"]["communes"] += 1
            causes["edition"]["entrees"] += max(net, 0)
            causes["edition"]["sorties"] += max(-net, 0)
        if _medianes(avant, code) != _medianes(apres, code):
            medianes_changees += 1
            sha_apres = (pred_apres.get(code) or {}).get("population_sha256")
            if sha_servi == sha_apres:
                inexpliques.append(
                    {
                        "code_insee": code,
                        "raison": "mediane_changee_sans_changement_de_population",
                    }
                )
    rapport["medianes_changees"] = medianes_changees
    rapport["causes"] = {
        cause: {k: causes[cause][k] for k in ("communes", "entrees", "sorties")}
        for cause in (*CAUSES, "edition")
        if cause in causes
    }
    rapport["inexpliques"] = inexpliques
    return rapport


def main(argv=None):
    parser = argparse.ArgumentParser(description="DVF fidelity from the archive")
    modes = parser.add_subparsers(dest="mode", required=True)
    for nom in ("archive", "empreintes"):
        sous = modes.add_parser(nom)
        sous.add_argument("--run", required=True)
        sous.add_argument("--racine", default=ARCHIVE_DVF)
    sql_base = modes.add_parser("sql-base")
    sql_base.add_argument("--communes", action="store_true")
    compare = modes.add_parser("compare")
    compare.add_argument("--base", required=True)
    compare.add_argument("--reference", required=True)
    compare.add_argument("--resume", required=True)
    kpi = modes.add_parser("kpi")
    kpi.add_argument("--avant", required=True)
    kpi.add_argument("--apres")
    kpi.add_argument("--reference", required=True)
    kpi.add_argument("--reference-servi")
    args = parser.parse_args(argv)
    if args.mode == "archive":
        rapport = mode_archive(args.racine, args.run)
    elif args.mode == "empreintes":
        mode_empreintes(args.racine, args.run, sys.stdout)
        return 0
    elif args.mode == "sql-base":
        print(mode_sql_base(communes=args.communes))
        return 0
    elif args.mode == "kpi":
        rapport = mode_kpi(args.avant, args.apres, args.reference, args.reference_servi)
    else:
        rapport = mode_compare(args.base, args.reference, args.resume)
    print(json.dumps(rapport, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
