"""Pure DVF qualification policy (Phase 57, D-01/D-05/D-10..D-13). No I/O of any kind.

ONE function decides the class of a reconstructed mutation: :func:`qualifie_mutation`.
It receives the flat counters already produced by :func:`compte_lignes` from the rows
of ``mutations`` grouped by ``id_mutation`` (D-01, the ETL reconstruction grain) and
returns a closed taxonomy plus an honest ``prix_m2``. Nothing here reads a database,
calls a service or touches the network -- the whole module is offline-testable, which
is why every rule below is proven row by row against the Pornichet-shaped and VEFA
fixtures (D-31) instead of being asserted through an endpoint.

The rules it enforces, and the substitution each one removes:

- ``compte_lignes`` counts the price ONCE per mutation regardless of how many rows
  carry it (DVF-01): it never sums ``valeur_fonciere`` across rows. A divergence
  between dispositions of the same ``id_mutation`` is a LOGGED GUARD, never an
  exception (Pattern 1, RESEARCH.md) -- the ETL run for 3.1M mutations cannot stop
  on one dirty record;
- ``nombre_lots`` is never summed, maxed or otherwise aggregated anywhere in this
  module (Pitfall 3, DVF-02): it is not even part of :class:`CompteursMutation` --
  the contract itself makes the aggregation impossible to write by accident;
- a mutation mixing a residential local (maison/appartement) and a commercial local
  under the same ``id_mutation`` is closed on ``mixte`` and NEVER attributable to a
  logement KPI, however many residential locals it also contains (D-05/D-10, the
  Pornichet-shaped configuration this phase was named for);
- more than one residential local closes on ``ensemble_residentiel`` -- an ensemble
  sale is never presented as the price of one isolated local (D-11);
- ``prix_m2`` is computed only for a mono-local residential mutation that is a real
  sale (``nature_mutation == "Vente"``) OR a VEFA (D-12's flag is orthogonal to the
  price computation, not to the classification) with a positive surface and a
  positive price. Every other case returns ``None`` with a reason drawn from the
  closed ``RAISONS_PRIX_M2`` vocabulary -- never a free string (same doctrine as
  ``app.resolve.schemas.RESOLUTION_RAISONS``);
- dependances (type 3) never count toward the residential/commercial local totals:
  their ``surface_reelle_bati`` is NULL by official DGFiP definition, so summing it
  is already correct without an explicit exclusion (DVF-01);
- an unreadable or degraded input (empty rows, an unrecognised ``code_type_local``)
  closes CONSERVATIVELY on ``hors_perimetre`` -- it never guesses in favour of the
  first row it happens to contain (same doctrine as ``app.dpe.attribution``);
- a local repeated on several parcels of the mutation, or on the culture lines
  of one parcel, counts once (:func:`compte_locaux`, phase 04.4 D-02 amended by
  variant V). This is the ONLY place of that rule: the ETL hands it the locals
  of each mutation as SQL arrays, the archive measurement hands it the source
  lines through :func:`compte_lignes`.
"""

import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# code_type_local (VERIFIED, notice DGFiP p.6): 1=Maison, 2=Appartement,
# 3=Dependance (isolee), 4=Local industriel et commercial ou assimiles.
# Empty/unrecognised means no built local -- see qualifie_mutation.
TYPE_MAISON = "1"
TYPE_APPARTEMENT = "2"
TYPE_DEPENDANCE = "3"
TYPE_COMMERCIAL = "4"

# nature_mutation values that gate the KPI/stats perimeter (D-12/D-13).
NATURE_VENTE = "Vente"
NATURE_VEFA = "Vente en l'état futur d'achèvement"

# Closed taxonomy exported to callers (57-05, 57-06, 57-09, 57-10). Order fixed
# by the phase interface contract -- never reorder without a coordinated bump.
CLASSES_MUTATION: tuple[str, ...] = (
    "residentiel_maison",
    "residentiel_appartement",
    "ensemble_residentiel",
    "mixte",
    "commerciale",
    "terrain_seul",
    "hors_perimetre",
)

# Closed vocabulary for why prix_m2 is None (D-15) -- never a free string.
RAISONS_PRIX_M2: tuple[str, ...] = (
    "mutation_multi_locaux",
    "vente_mixte",
    "mutation_commerciale",
    "surface_absente",
    "terrain_seul",
    "nature_hors_vente",
)

_RESIDENTIEL_MONO: frozenset[str] = frozenset(
    {"residentiel_maison", "residentiel_appartement"}
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompteursMutation:
    """Flat counters for the rows of ONE ``id_mutation`` (D-08: no JSONB, plat)."""

    nb_locaux_maison: int
    nb_locaux_appartement: int
    nb_locaux_commercial: int
    nb_locaux_dependance: int
    nb_lignes: int
    nb_dispositions: int
    nb_communes: int
    nb_parcelles: int
    surface_bati_totale: int | None
    surface_terrain_totale: int | None
    nombre_pieces_totales: int | None
    nature_mutation: str | None
    valeur_fonciere: Decimal | None
    valeur_divergente: bool


@dataclass(frozen=True)
class ResultatQualification:
    """The published decision for one mutation (D-15)."""

    classe_mutation: str
    mono_local: bool
    est_vente: bool
    hors_stats_vefa: bool
    prix_m2: Decimal | None
    prix_m2_raison: str | None


@dataclass(frozen=True)
class ComptesLocaux:
    """The six local counters of ONE mutation, repeated locals merged (D-02 V)."""

    nb_locaux_maison: int
    nb_locaux_appartement: int
    nb_locaux_commercial: int
    nb_locaux_dependance: int
    surface_bati_totale: int | None
    nombre_pieces_totales: int | None
    surface_terrain_totale: int | None


# One source line seen as a local: (numero_disposition, code_type_local,
# surface_reelle_bati, nombre_pieces_principales, id_parcelle,
# code_nature_culture, surface_terrain).
Local = tuple[
    str | None, str | None, int | None, int | None, str | None, str | None, int | None
]


def compte_locaux(locaux: Iterable[Local]) -> ComptesLocaux:
    """Count the locals of one mutation, a repeated local once (D-02, variant V).

    The source has no local identifier. DGFiP repeats a building that spans
    several parcels on one line per parcel, and repeats every local of a
    parcel on each culture line of that parcel (S, J, AG, T...). The
    signature of a local is ``(disposition, code_type_local,
    surface_reelle_bati, nombre_pieces_principales)``; its multiplicity is
    the MAXIMUM, over the pairs ``(id_parcelle, code_nature_culture)`` (NULL
    a value like any other in both), of its number of lines on that pair.
    Two identical lines on one parcel and one culture stay two locals
    (identical lots of a programme); the same line on two parcels, or on two
    culture lines of one parcel, is one local.

    Variant V of D-02 was retained by Ianis on 2026-10-21 (04.4-CONTEXT.md):
    D-02 alone kept 7 of the 15 repeated locals mono-local (plan 04.4-03).
    This is a heuristic: two identical houses on two parcels, or on two
    cultures of one parcel, merge into one. The rooms of a local counted once
    count once, like its surface.

    ``nb_locaux_<type>`` sums the multiplicities by ``code_type_local``;
    ``surface_bati_totale`` and ``nombre_pieces_totales`` sum value times
    multiplicity over the non-null values, None if no line reported any.

    The land surface follows the same repetition (IN-05 of the 04.4 review):
    each culture line of a parcel is repeated for every local of that
    parcel, so summing all lines counted a parcel with a house and two
    dependances three times. On a pair ``(id_parcelle, code_nature_culture)``
    every land value is therefore repeated once per local; the repetition is
    the smallest count among the distinct values of the pair, and the land
    of the pair sums each value times its count divided by that repetition
    (several fiscal subdivisions of one culture still add up). The total
    sums the pairs, None if no line reported a land surface. Heuristic
    limit: two subdivisions of one culture with the same surface, on a
    parcel carrying no other value of that culture, count once.
    """
    entrees = list(locaux)
    par_parcelle_culture: Counter[tuple[tuple[Any, ...], str | None, str | None]] = (
        Counter(
            ((disposition, code_type, surface, pieces), parcelle, culture)
            for disposition, code_type, surface, pieces, parcelle, culture, _ in entrees
        )
    )
    terrain_par_paire: dict[tuple[str | None, str | None], Counter[int]] = {}
    for *_, parcelle, culture, terrain in entrees:
        if terrain is not None:
            terrain_par_paire.setdefault((parcelle, culture), Counter())[terrain] += 1
    multiplicites: dict[tuple[Any, ...], int] = {}
    for (signature, _parcelle, _culture), lignes in par_parcelle_culture.items():
        multiplicites[signature] = max(multiplicites.get(signature, 0), lignes)

    par_type: Counter[str | None] = Counter()
    for (_disposition, code_type, _surface, _pieces), n in multiplicites.items():
        par_type[code_type] += n

    def total(rang: int) -> int | None:
        return _somme_optionnelle(
            None if signature[rang] is None else signature[rang] * n
            for signature, n in multiplicites.items()
        )

    return ComptesLocaux(
        nb_locaux_maison=par_type[TYPE_MAISON],
        nb_locaux_appartement=par_type[TYPE_APPARTEMENT],
        nb_locaux_commercial=par_type[TYPE_COMMERCIAL],
        nb_locaux_dependance=par_type[TYPE_DEPENDANCE],
        surface_bati_totale=total(2),
        nombre_pieces_totales=total(3),
        surface_terrain_totale=_somme_optionnelle(
            _terrain_de_paire(valeurs) for valeurs in terrain_par_paire.values()
        ),
    )


def _terrain_de_paire(valeurs: Counter[int]) -> int:
    """Land of one (parcel, culture) pair, its repetition per local removed."""
    repetition = min(valeurs.values())
    return sum(valeur * round(n / repetition) for valeur, n in valeurs.items())


def _somme_optionnelle(valeurs: Iterable[int | None]) -> int | None:
    """Sum the non-None values, or None if every value was absent.

    ``None`` here means "no source line reported this measure at all" -- distinct
    from a genuine zero, which a dependance's null surface never contributes.
    """
    presentes = [valeur for valeur in valeurs if valeur is not None]
    return sum(presentes) if presentes else None


def _compte_valeurs_distinctes(lignes: Sequence[Mapping[str, Any]]) -> set[Decimal]:
    # A declared 0 is a value, only None is an absence: the same rule as the
    # SQL reconstruction (max / count(DISTINCT) skip NULL, never 0).
    return {
        ligne["valeur_fonciere"]
        for ligne in lignes
        if ligne.get("valeur_fonciere") is not None
    }


def compte_lignes(lignes: Sequence[Mapping[str, Any]]) -> CompteursMutation:
    """Reduce the rows of one ``id_mutation`` to flat counters.

    *lignes* is assumed already grouped by ``id_mutation`` upstream (the ETL
    reconstruction step, D-01/D-04) -- this function does not group, it only
    counts. An empty sequence is a valid, degraded input and never raises.
    """
    lignes = list(lignes)

    locaux = compte_locaux(
        (
            ligne.get("numero_disposition"),
            ligne.get("code_type_local"),
            ligne.get("surface_reelle_bati"),
            ligne.get("nombre_pieces_principales"),
            ligne.get("id_parcelle"),
            ligne.get("code_nature_culture"),
            ligne.get("surface_terrain"),
        )
        for ligne in lignes
    )

    valeurs = _compte_valeurs_distinctes(lignes)
    valeur_divergente = len(valeurs) > 1
    if valeur_divergente:
        id_mutation = lignes[0].get("id_mutation") if lignes else None
        _LOGGER.warning(
            "valeur_fonciere divergente pour id_mutation=%s: %s",
            id_mutation,
            sorted(valeurs),
        )

    nature_mutation = next(
        (
            ligne.get("nature_mutation")
            for ligne in lignes
            if ligne.get("nature_mutation")
        ),
        None,
    )

    return CompteursMutation(
        nb_locaux_maison=locaux.nb_locaux_maison,
        nb_locaux_appartement=locaux.nb_locaux_appartement,
        nb_locaux_commercial=locaux.nb_locaux_commercial,
        nb_locaux_dependance=locaux.nb_locaux_dependance,
        nb_lignes=len(lignes),
        nb_dispositions=_compte_distincts(lignes, "numero_disposition"),
        nb_communes=_compte_distincts(lignes, "code_commune"),
        nb_parcelles=_compte_distincts(lignes, "id_parcelle"),
        surface_bati_totale=locaux.surface_bati_totale,
        surface_terrain_totale=locaux.surface_terrain_totale,
        nombre_pieces_totales=locaux.nombre_pieces_totales,
        nature_mutation=nature_mutation,
        valeur_fonciere=max(valeurs) if valeurs else None,
        valeur_divergente=valeur_divergente,
    )


def _compte_distincts(lignes: Sequence[Mapping[str, Any]], champ: str) -> int:
    return len({ligne[champ] for ligne in lignes if ligne.get(champ)})


def _classe(compteurs: CompteursMutation) -> str:
    """D-10..D-13 classification, dependances never counted as a built local."""
    n_res = compteurs.nb_locaux_maison + compteurs.nb_locaux_appartement
    n_com = compteurs.nb_locaux_commercial
    n_bati = n_res + n_com

    if n_bati == 0:
        if (compteurs.surface_terrain_totale or 0) > 0:
            return "terrain_seul"
        return "hors_perimetre"
    if n_res >= 1 and n_com >= 1:
        return "mixte"
    if n_com >= 1:
        return "commerciale"
    if n_res == 1:
        if compteurs.nb_locaux_maison == 1:
            return "residentiel_maison"
        return "residentiel_appartement"
    return "ensemble_residentiel"


def _prix_m2(
    classe: str, mono_local: bool, vendable: bool, compteurs: CompteursMutation
) -> tuple[Decimal | None, str | None]:
    """Honest prix_m2 (D-15): only a mono-local residential sale, ever."""
    if classe == "mixte":
        return None, "vente_mixte"
    if classe == "commerciale":
        return None, "mutation_commerciale"
    if classe == "terrain_seul":
        return None, "terrain_seul"
    if classe not in _RESIDENTIEL_MONO:
        # ensemble_residentiel ou hors_perimetre : jamais un prix de bien isole.
        return None, "mutation_multi_locaux"
    if not vendable:
        return None, "nature_hors_vente"

    surface = compteurs.surface_bati_totale
    valeur = compteurs.valeur_fonciere
    if not mono_local or not surface or surface <= 0 or not valeur or valeur <= 0:
        return None, "surface_absente"

    return valeur / Decimal(surface), None


def qualifie_mutation(compteurs: CompteursMutation) -> ResultatQualification:
    """Decide the closed classification and the honest prix_m2 of one mutation."""
    classe = _classe(compteurs)
    n_bati = (
        compteurs.nb_locaux_maison
        + compteurs.nb_locaux_appartement
        + compteurs.nb_locaux_commercial
    )
    mono_local = n_bati == 1

    est_vente = compteurs.nature_mutation == NATURE_VENTE
    hors_stats_vefa = compteurs.nature_mutation == NATURE_VEFA
    # A VEFA is a form of sale (future-delivery) -- eligible for a per-mutation
    # prix_m2 even though D-12 excludes it from AGGREGATED stats separately.
    vendable = est_vente or hors_stats_vefa

    prix_m2, prix_m2_raison = _prix_m2(classe, mono_local, vendable, compteurs)

    return ResultatQualification(
        classe_mutation=classe,
        mono_local=mono_local,
        est_vente=est_vente,
        hors_stats_vefa=hors_stats_vefa,
        prix_m2=prix_m2,
        prix_m2_raison=prix_m2_raison,
    )
