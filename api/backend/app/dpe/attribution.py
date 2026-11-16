"""Pure V2 DPE attribution policy with no I/O.

An address-level inventory is evidence that documents exist at the source BAN
key. It is never proof that one document belongs to the searched dwelling.
"""

from collections.abc import Sequence

from app.dpe.schemas import DpeAttribution, DpeBuildingContext

CLASSES_DPE: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G")


def _contient_cycle(par_numero: dict[str, dict]) -> bool:
    """Return whether a replacement chain loops inside the loaded set."""
    for depart, ligne in par_numero.items():
        vus = {depart}
        courant = ligne.get("numero_dpe_remplace")
        while courant in par_numero:
            if courant in vus:
                return True
            vus.add(courant)
            courant = par_numero[courant].get("numero_dpe_remplace")
    return False


def _terminaux(rows: Sequence[dict]) -> tuple[list[dict], bool]:
    """Reduce contextual rows and report an unusable replacement graph."""
    par_numero: dict[str, dict] = {}
    doublon_incoherent = False
    for row in rows:
        numero = row.get("numero_dpe")
        if not numero:
            doublon_incoherent = True
            continue
        if numero in par_numero and par_numero[numero] != row:
            doublon_incoherent = True
        par_numero[numero] = row

    remplaces: dict[str, int] = {}
    for row in par_numero.values():
        cible = row.get("numero_dpe_remplace")
        if cible:
            remplaces[cible] = remplaces.get(cible, 0) + 1

    terminaux = [row for numero, row in par_numero.items() if numero not in remplaces]
    fourche = any(compte > 1 for compte in remplaces.values())
    inutilisable = doublon_incoherent or fourche or _contient_cycle(par_numero)
    return terminaux, inutilisable or (bool(par_numero) and not terminaux)


def _distribution(rows: Sequence[dict]) -> dict[str, int]:
    """Count known A-G labels without electing a representative class."""
    compte: dict[str, int] = {}
    for row in rows:
        classe = (row.get("etiquette_dpe") or "").strip().upper()
        if classe in CLASSES_DPE:
            compte[classe] = compte.get(classe, 0) + 1
    return {classe: compte[classe] for classe in CLASSES_DPE if classe in compte}


def _context_other_addresses(
    rows: Sequence[dict],
    *,
    truncated: bool,
) -> DpeBuildingContext | None:
    """Project other BAN keys as a separate aggregate, never as candidates."""
    terminaux, graph_incoherent = _terminaux(rows)
    aggregate = list(rows if graph_incoherent else terminaux)
    incomplete = truncated or graph_incoherent
    if not aggregate and not incomplete:
        return None
    return DpeBuildingContext(
        count=len(aggregate),
        distribution=_distribution(aggregate),
        truncated=incomplete,
    )


def classify_dpe_attribution(
    ban_id: str | None,
    exact_rows: Sequence[dict],
    *,
    building_rows: Sequence[dict] = (),
    exact_truncated: bool = False,
    building_truncated: bool = False,
) -> DpeAttribution:
    """Return a non-attributive V2 decision for the resolved address."""
    if not ban_id:
        return DpeAttribution(status="absent", reason="identity_unavailable")

    exact = [row for row in exact_rows if row.get("identifiant_ban") == ban_id]
    _, exact_graph_incoherent = _terminaux(exact)
    other_address_rows = [
        row
        for row in building_rows
        if isinstance((source_ban := row.get("identifiant_ban")), str)
        and (normalized_ban := source_ban.strip())
        and normalized_ban != ban_id
    ]
    context = _context_other_addresses(
        other_address_rows,
        truncated=building_truncated,
    )
    graph_state = "incoherent" if exact_graph_incoherent else "not_checked"

    if exact or exact_truncated:
        return DpeAttribution(
            status="unassigned",
            reason="dwelling_identity_unproven",
            building_context=context,
            graph_state=graph_state,
        )
    return DpeAttribution(
        status="absent",
        reason="not_reported",
        building_context=context,
        graph_state=graph_state,
    )
