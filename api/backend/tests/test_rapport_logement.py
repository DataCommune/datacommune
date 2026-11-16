"""Housing themes of the address report (04.7-08), without a database.

The tense zone and the rent control are read live, at the commune (an
arrondissement on its commune): a list, never a verdict on the address. An
order of rent control is said in force, to come or ended from its own dates
against the date of the report, never extended.
"""

import httpx
import pytest
import respx

from app.common.exceptions import UpstreamUnavailableError
from app.rapport import partage
from app.rapport.partage import EncadrementLu, PeriodeEncadrement, ZoneTendueLue
from app.rapport.socle import PanneAmont
from app.rapport.themes import encadrement_loyers, zone_tendue
from tests.test_rapport_themes import (  # noqa: F401 (autouse fixtures)
    AS_OF,
    _contexte,
    _publication_validee,
    _requete,
    _sans_provenance,
)
from tests.test_rapport_vivre2 import _amont, _avec_commune

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _caches_vides():
    partage.vider_caches()
    yield
    partage.vider_caches()


# --- Zone tendue : lecteur ---------------------------------------------------------


def _entree(code, ok=True):
    return {"id": 1, "Nom": "X", "codeInsee": code, "Ok": ok}


def _zones(agglomeration=("44109", "75056"), desequilibre=("85194",), remplir=True):
    plus = [_entree(c) for c in agglomeration]
    autres = [_entree(c) for c in desequilibre]
    if remplir:  # two lists of the national size
        n = partage.ZONE_TENDUE_MINIMUM
        plus += [_entree(f"{10000 + i}") for i in range(n)]
        autres += [_entree(f"{20000 + i}") for i in range(n)]
    return {"PlusDe50000": plus, "DesequilibreOffreEtDemande": autres}


def test_the_two_lists_give_the_category_of_each_commune():
    zones = partage.zones_tendues_de(_zones())
    assert zones["44109"] == "agglomeration"
    assert zones["85194"] == "desequilibre"
    assert "85047" not in zones


def test_an_entry_not_ok_is_an_outage_never_a_commune_dropped():
    # « Ok » is true on every entry and its meaning is not documented.
    donnees = _zones()
    donnees["PlusDe50000"].append(_entree("85047", ok=False))
    with pytest.raises(UpstreamUnavailableError):
        partage.zones_tendues_de(donnees)


def test_one_list_truncated_is_an_outage():
    donnees = _zones()
    donnees["PlusDe50000"] = donnees["PlusDe50000"][:2]
    with pytest.raises(UpstreamUnavailableError):
        partage.zones_tendues_de(donnees)


@pytest.mark.parametrize(
    "donnees",
    [
        [],
        {"PlusDe50000": []},  # one list missing
        _zones(remplir=False),  # far fewer communes than the national list
        _zones(desequilibre=("44109",)),  # a commune in both lists
        {**_zones(), "PlusDe50000": [{"codeInsee": "4410", "Ok": True}]},
        {**_zones(), "PlusDe50000": [{"codeInsee": "44109", "Ok": "oui"}]},
    ],
)
def test_an_unreadable_list_is_an_outage(donnees):
    with pytest.raises(UpstreamUnavailableError):
        partage.zones_tendues_de(donnees)


async def test_the_list_is_read_once_and_an_arrondissement_on_its_commune():
    with respx.mock() as mock:
        route = mock.get(partage.ZONE_TENDUE_URL).mock(
            return_value=httpx.Response(200, json=_zones())
        )
        nantes = await partage.zone_tendue_au_point(_amont(_contexte()))
        paris = await partage.zone_tendue_au_point(
            _amont(_avec_commune(_contexte(), "75112", "Paris 12e"))
        )
    assert nantes == ZoneTendueLue(commune="44109", categorie="agglomeration")
    assert paris == ZoneTendueLue(commune="75056", categorie="agglomeration")
    assert route.call_count == 1


async def test_a_tense_zone_outage_is_never_kept():
    with respx.mock() as mock:
        route = mock.get(partage.ZONE_TENDUE_URL).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json=_zones())]
        )
        premier = await partage.zone_tendue_au_point(_amont(_contexte()))
        second = await partage.zone_tendue_au_point(_amont(_contexte()))
    assert isinstance(premier, PanneAmont)
    assert second.categorie == "agglomeration"
    assert route.call_count == 2


async def test_a_tense_zone_not_hosted_reads_nothing():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(partage.ZONE_TENDUE_URL)
        lu = await partage.zone_tendue_au_point(
            _amont(_contexte(hors=("zone_tendue",)))
        )
    assert lu is None and route.call_count == 0


# --- Zone tendue : thème -----------------------------------------------------------


async def _zone(lu, contexte=None):
    contexte = contexte or _contexte()
    contexte.amonts["zone_tendue"] = lu
    return await zone_tendue.lire(contexte)


@pytest.mark.parametrize("categorie", ["agglomeration", "desequilibre"])
async def test_a_tense_zone_is_a_neutral_classification(categorie):
    theme = await _zone(ZoneTendueLue(commune="44109", categorie=categorie))
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert "taxe sur les logements vacants" in theme.verdict
    # « Zone tendue » is said of the urban areas only.
    assert ("zone tendue" in theme.verdict) == (categorie == "agglomeration")
    assert theme.details["categorie"] == categorie
    assert theme.granularite.niveau == "commune"
    assert theme.chiffres == []


async def test_a_commune_out_of_the_lists_is_not_concerned():
    theme = await _zone(ZoneTendueLue(commune="85047", categorie=None))
    assert (theme.etat, theme.ton) == ("non_concerne", "neutre")
    assert "n'est pas en zone tendue" in theme.verdict


async def test_an_arrondissement_is_said_read_on_its_commune():
    contexte = _avec_commune(_contexte(), "75112", "Paris 12e")
    theme = await _zone(
        ZoneTendueLue(commune="75056", categorie="agglomeration"), contexte
    )
    assert theme.details["commune"] == "75056"
    assert theme.details["lue_sur_la_commune"] is True


async def test_a_tense_zone_outage_out_of_coverage_and_gap_are_told_apart():
    panne = await _zone(PanneAmont())
    manque = await _zone(None)
    hors = await zone_tendue.lire(_contexte(hors=("zone_tendue",)))
    assert (panne.etat, manque.etat, hors.etat) == (
        "panne",
        "manque",
        "hors_couverture",
    )
    assert manque.motif == "commune_indeterminee"


# --- Encadrement des loyers : lecteur ----------------------------------------------

ENTETE = (
    "code_departement,departement,code_insee,commune,territoire,zone,"
    "collectivite_demandeuse,decret_perimetre,decret_url,arrete_prefectoral,"
    "arrete_date,en_vigueur_du,en_vigueur_au,loyers_reference_url,observatoire,"
    "note,source_libelle,source_url,source_type,source_date,verifie_le"
)


def _ligne(code="75056", zone="commune", du="2026-07-01", au="2026-11-24"):
    return (
        f"75,Paris,{code},Paris,Paris,{zone},Ville de Paris,Décret n° 2019-315,"
        f"https://www.legifrance.gouv.fr/x,IDF-2026-06-12-00003,2026-06-12,{du},{au},"
        ",,,Arrêté préfectoral,https://cdn.paris.fr/a.pdf,arrete,2026-06-12,2026-10-29"
    )


def _csv(*lignes, complet=True):
    remplissage = (
        [_ligne(f"{1001 + i:05d}") for i in range(partage.ENCADREMENT_MINIMUM)]
        if complet
        else []
    )
    return "\n".join((ENTETE, *lignes, *remplissage)) + "\n"


def test_the_list_gives_the_orders_of_each_commune():
    communes = partage.encadrement_de(_csv(_ligne(), _ligne("93066", zone="partie")))
    paris = communes["75056"][0]
    assert (paris.du.isoformat(), paris.au.isoformat()) == ("2026-07-01", "2026-11-24")
    assert paris.arrete == "IDF-2026-06-12-00003"
    assert paris.verifie_le.isoformat() == "2026-10-29"
    assert communes["93066"][0].zone == "partie"


def test_an_order_without_end_keeps_no_end():
    communes = partage.encadrement_de(_csv(_ligne("64102", du="2025-11-25", au="")))
    assert communes["64102"][0].au is None


@pytest.mark.parametrize(
    "texte",
    [
        "code_insee,commune\n75056,Paris\n",  # columns missing
        _csv(_ligne(), complet=False),  # a list far shorter than the national one
        _csv(_ligne(code="7505")),
        _csv(_ligne(du="01/07/2026")),
        _csv(_ligne(zone="")),  # a perimeter not said
        _csv(_ligne(zone="partielle")),  # a perimeter not known
    ],
)
def test_an_unreadable_rent_control_list_is_an_outage(texte):
    with pytest.raises(UpstreamUnavailableError):
        partage.encadrement_de(texte)


async def test_the_rent_control_list_follows_its_link_and_is_read_once():
    with respx.mock() as mock:
        mock.get(partage.ENCADREMENT_URL).mock(
            return_value=httpx.Response(
                302, headers={"location": "https://static.data.gouv.fr/x.csv"}
            )
        )
        fichier = mock.get("https://static.data.gouv.fr/x.csv").mock(
            return_value=httpx.Response(200, text=_csv(_ligne()))
        )
        paris = await partage.encadrement_au_point(
            _amont(_avec_commune(_contexte(), "75112", "Paris 12e"))
        )
        nantes = await partage.encadrement_au_point(_amont(_contexte()))
    assert paris.commune == "75056" and len(paris.periodes) == 1
    assert nantes.commune == "44109" and nantes.periodes == ()
    assert nantes.liste_verifiee_le.isoformat() == "2026-10-29"
    assert fichier.call_count == 1


def _coupee():
    # A file cut in a row, after the perimeter: the cells left are absent.
    complet = _csv(_ligne())
    lignes = complet.splitlines()
    coupee = ",".join(lignes[1].split(",")[:6])
    return "\n".join([lignes[0], coupee, *lignes[2:]]) + "\n"


@pytest.mark.parametrize("texte", ["coupee", "longue"])
def test_a_malformed_row_is_an_outage(texte):
    donnees = _coupee() if texte == "coupee" else _csv(_ligne() + ",en trop")
    with pytest.raises(UpstreamUnavailableError):
        partage.encadrement_de(donnees)


async def test_a_cut_list_is_never_kept():
    with respx.mock() as mock:
        route = mock.get(partage.ENCADREMENT_URL).mock(
            side_effect=[
                httpx.Response(200, text=_coupee()),
                httpx.Response(200, text=_csv(_ligne())),
            ]
        )
        contexte = _avec_commune(_contexte(), "75056", "Paris")
        premier = await partage.encadrement_au_point(_amont(contexte))
        second = await partage.encadrement_au_point(_amont(contexte))
    assert isinstance(premier, PanneAmont)
    assert len(second.periodes) == 1
    assert route.call_count == 2


async def test_a_rent_control_outage_is_never_kept():
    with respx.mock() as mock:
        route = mock.get(partage.ENCADREMENT_URL).mock(
            side_effect=[httpx.Response(500), httpx.Response(200, text=_csv(_ligne()))]
        )
        contexte = _avec_commune(_contexte(), "75056", "Paris")
        premier = await partage.encadrement_au_point(_amont(contexte))
        second = await partage.encadrement_au_point(_amont(contexte))
    assert isinstance(premier, PanneAmont)
    assert len(second.periodes) == 1
    assert route.call_count == 2


# --- Encadrement des loyers : thème ------------------------------------------------


def _periode(du="2026-07-01", au="2026-11-24", zone="commune"):
    from datetime import date

    return PeriodeEncadrement(
        territoire="Paris",
        zone=zone,
        decret="Décret n° 2019-315",
        arrete="IDF-2026-06-12-00003",
        arrete_date=date(2026, 6, 12),
        du=date.fromisoformat(du) if du else None,
        au=date.fromisoformat(au) if au else None,
        source="Arrêté préfectoral",
        source_url="https://cdn.paris.fr/a.pdf",
        verifie_le=date(2026, 10, 29),
    )


async def _encadrement(*periodes, contexte=None):
    from datetime import date

    contexte = contexte or _contexte()
    contexte.amonts["encadrement_loyers"] = EncadrementLu(
        commune="75056", periodes=tuple(periodes), liste_verifiee_le=date(2026, 10, 29)
    )
    return await encadrement_loyers.lire(contexte)


async def test_an_order_in_force_says_its_end():
    theme = await _encadrement(_periode())
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert theme.verdict == "Loyers encadrés jusqu'au 24 novembre 2026."
    assert theme.details["statut"] == "en_vigueur"
    assert theme.details["loyers_reference"]["etat"] == "hors_couverture"
    assert theme.sources[0].millesime == "2026-10-29"


async def test_an_order_past_its_end_is_ended_never_extended():
    ancien = _periode(du="2025-07-01", au="2026-06-30")
    theme = await _encadrement(ancien)
    assert theme.details["statut"] == "echu"
    assert theme.verdict.startswith(
        "Arrêté d'encadrement des loyers échu le 30 juin 2026"
    )


async def test_an_order_to_come_is_said_to_come():
    theme = await _encadrement(_periode(du="2026-11-01", au="2026-11-23"))
    assert theme.details["statut"] == "a_venir"
    assert "à partir du 1er novembre 2026" in theme.verdict
    # Lyon on 31/10/2026: the order before is unknown, never said absent.
    assert "l'arrêté précédent n'est pas dans la liste" in theme.verdict


async def test_the_order_in_force_wins_over_a_later_one_to_come():
    theme = await _encadrement(
        _periode(du="2026-11-25", au=""), _periode(du="2026-07-01", au="2026-11-24")
    )
    assert theme.details["statut"] == "en_vigueur"
    assert theme.details["en_vigueur_au"] == "2026-11-24"


async def test_an_order_without_end_says_its_start_and_no_end():
    theme = await _encadrement(_periode(du="2025-11-25", au=""))
    assert theme.verdict == (
        "Loyers encadrés depuis le 25 novembre 2025, sans date de fin publiée."
    )


async def test_an_order_without_end_is_not_read_beyond_the_experiment():
    from datetime import date

    contexte = _contexte()
    apres = contexte.__class__(**{**contexte.__dict__, "as_of": date(2026, 11, 25)})
    theme = await _encadrement(_periode(du="2025-11-25", au=""), contexte=apres)
    assert (theme.etat, theme.motif) == ("manque", "fin_non_publiee")


async def test_part_of_the_commune_never_places_the_address():
    theme = await _encadrement(_periode(zone="partie"))
    assert theme.details["perimetre"] == "partie"
    assert "une partie de la commune" in theme.verdict
    assert "l'adresse n'est pas située" in theme.verdict
    assert len(theme.verdict) <= 140


async def test_a_commune_out_of_the_list_is_said_absent_from_the_dated_list():
    theme = await _encadrement()
    assert (theme.etat, theme.ton) == ("non_concerne", "neutre")
    assert "n'est pas dans la liste" in theme.verdict
    assert theme.sources[0].millesime == "2026-10-29"


@pytest.mark.parametrize("au", ["", "2026-11-24"])
async def test_an_order_without_start_is_a_gap_without_status(au):
    theme = await _encadrement(_periode(du="", au=au))
    assert (theme.etat, theme.motif) == ("manque", "dates_non_publiees")
    assert theme.details["statut"] is None


@pytest.mark.parametrize("zone", ["commune", "partie"])
@pytest.mark.parametrize(
    ("du", "au"),
    [("2026-09-30", "2026-11-30"), ("2026-09-30", ""), ("2026-11-30", "2027-09-30"),
     ("2025-09-30", "2026-09-30")],
)  # fmt: skip
async def test_every_rent_control_verdict_fits(zone, du, au):
    # The longest month names, every state and both perimeters.
    theme = await _encadrement(_periode(du=du, au=au, zone=zone))
    assert theme.etat == "ok"
    assert len(theme.verdict) <= 140
    if zone == "partie" and theme.details["statut"] != "echu":
        assert "partie de la commune" in theme.verdict


async def test_rent_control_outage_out_of_coverage_and_gap_are_told_apart():
    contexte = _contexte()
    contexte.amonts["encadrement_loyers"] = PanneAmont()
    panne = await encadrement_loyers.lire(contexte)
    hors = await encadrement_loyers.lire(_contexte(hors=("encadrement_communes",)))
    assert (panne.etat, hors.etat) == ("panne", "hors_couverture")


def test_as_of_of_the_report_tests_is_before_the_end_of_the_orders():
    # The verdicts above read « en vigueur » on the date of the report tests.
    assert AS_OF.isoformat() == "2026-10-30"


# --- Taxe foncière : moyenne du département, majoration -----------------------------


def _fiscalite(monkeypatch, ligne, moyenne=None):
    from app.rapport.themes import fiscalite

    async def lignes(session, codes):
        return [ligne]

    appels = []

    async def lue(session, departement, annee):
        appels.append((departement, annee))
        if isinstance(moyenne, Exception):
            raise moyenne
        return moyenne

    monkeypatch.setattr(fiscalite, "fiscalite_par_codes", lignes)
    monkeypatch.setattr(fiscalite, "moyenne_departement", lue)
    return fiscalite, appels


LIGNE_NANTES = {
    "code_insee": "44109",
    "code_departement": "44",
    "nom_departement": "Loire-Atlantique",
    "taux_tfpb": 53.37,
    "annee": 2025,
    "majoration_thrs": True,
    "taux_majoration_thrs": 60.0,
}


async def test_the_global_rate_is_compared_to_the_plain_mean_of_the_departement(
    monkeypatch,
):
    module, appels = _fiscalite(
        monkeypatch, LIGNE_NANTES, {"moyenne": 41.2345, "communes": 207, "taux": 207}
    )
    theme = await module.lire(_contexte())
    assert theme.verdict.startswith("Taux global de taxe foncière sur le bâti")
    assert "53,37 % en 2025" in theme.verdict
    moyenne = theme.details["moyenne_departement"]
    assert moyenne == {
        "etat": "ok",
        "departement": "44",
        "nom_departement": "Loire-Atlantique",
        "moyenne_simple": 41.23,
        "communes": 207,
        "ecart_points": 12.14,
        "annee": 2025,
    }
    assert appels == [("44", 2025)]
    assert theme.ton == "neutre"


async def test_no_mean_over_a_departement_not_hosted(monkeypatch):
    ligne = {**LIGNE_NANTES, "code_departement": "49"}
    module, appels = _fiscalite(monkeypatch, ligne, {"moyenne": 40, "communes": 2})
    theme = await module.lire(_contexte())
    assert theme.details["moyenne_departement"]["etat"] == "hors_couverture"
    assert appels == []


async def test_a_mean_over_part_of_the_communes_is_a_gap(monkeypatch):
    module, _ = _fiscalite(
        monkeypatch, LIGNE_NANTES, {"moyenne": 40.0, "communes": 207, "taux": 1}
    )
    theme = await module.lire(_contexte())
    assert theme.details["moyenne_departement"] == {
        "etat": "manque",
        "motif": "taux_manquants_au_departement",
    }


async def test_a_departement_of_one_commune_has_no_comparison(monkeypatch):
    module, _ = _fiscalite(monkeypatch, LIGNE_NANTES, {"moyenne": 53.37, "communes": 1})
    theme = await module.lire(_contexte())
    assert theme.details["moyenne_departement"]["etat"] == "non_concerne"


async def test_an_outage_of_the_mean_is_declared_never_the_state(monkeypatch):
    from sqlalchemy.exc import OperationalError

    module, _ = _fiscalite(
        monkeypatch, LIGNE_NANTES, OperationalError("x", None, Exception())
    )
    theme = await module.lire(_contexte())
    assert theme.etat == "ok"
    assert theme.details["moyenne_departement"]["etat"] == "panne"
    assert theme.pannes_partielles == ["fiscalite"]


@pytest.mark.parametrize(
    ("majoration", "taux", "attendu"),
    [
        (True, 60.0, {"etat": "ok", "majoree": True, "taux": 60.0}),
        (False, 0.0, {"etat": "ok", "majoree": False, "taux": None}),
        (None, None, {"etat": "hors_couverture", "motif": "majoration_non_importee"}),
    ],
)
async def test_the_surcharge_on_secondary_residences(
    monkeypatch, majoration, taux, attendu
):
    ligne = {
        **LIGNE_NANTES,
        "majoration_thrs": majoration,
        "taux_majoration_thrs": taux,
    }
    module, _ = _fiscalite(
        monkeypatch, ligne, {"moyenne": 40, "communes": 2, "taux": 2}
    )
    theme = await module.lire(_contexte())
    assert theme.details["majoration_thrs"] == attendu


# --- Copropriété : syndic et mandat ------------------------------------------------


def _registre(**gestion):
    return {
        "numero_immatriculation": "AA1",
        "nb_lots_total": 24,
        "nb_lots_habitation_strict": 18,
        "type_syndic": "professionnel",
        "nom_syndic": "Cabinet Exemple",
        "mandat_en_cours": "mandat en cours",
        "date_fin_dernier_mandat": None,
        **gestion,
    }


def test_a_professional_manager_is_named_with_its_mandate():
    from datetime import date

    from app.rapport.themes.copropriete import syndic_de

    syndic = syndic_de(_registre(date_fin_dernier_mandat=date(2027, 6, 30)))
    assert syndic == {
        "type": "professionnel",
        "nom": "Cabinet Exemple",
        "mandat": {
            "etat": "ok",
            "statut": "en_cours",
            "fin_dernier_mandat": "2027-06-30",
        },
    }


@pytest.mark.parametrize(
    ("statut", "motif"),
    [("non_connu", "mandat_non_renseigne"), ("mandat suspendu", "statut_inconnu")],
)
def test_a_mandate_left_empty_or_unknown_is_a_gap(statut, motif):
    from app.rapport.themes.copropriete import syndic_de

    syndic = syndic_de(_registre(mandat_en_cours=statut))
    assert syndic["mandat"] == {"etat": "manque", "motif": motif}


@pytest.mark.parametrize(
    "type_syndic", ["bénévole", "Benevole", "coopératif", None, "?"]
)
def test_a_volunteer_or_unknown_manager_is_never_named(type_syndic):
    from app.rapport.themes.copropriete import syndic_de

    syndic = syndic_de(_registre(type_syndic=type_syndic, nom_syndic="Jean Dupont"))
    assert syndic["nom"] is None
    assert syndic["type"] in {"benevole", "cooperatif", "non_connu"}


def test_a_mandate_not_imported_is_out_of_coverage():
    from app.rapport.themes.copropriete import syndic_de

    syndic = syndic_de(_registre(mandat_en_cours=None))
    assert syndic["mandat"] == {
        "etat": "hors_couverture",
        "motif": "mandat_non_importe",
    }


async def test_the_report_reads_the_manager_and_the_public_query_does_not(monkeypatch):
    from app.adresse.blocs import Bloc
    from app.logements import queries
    from app.rapport.themes import copropriete

    appels = []

    async def lue(session, id_parcelle, limit=5, *, avec_gestion=False):
        appels.append(avec_gestion)
        return [_registre()]

    async def bloc(session, sources, lecteur):
        return Bloc(valeur=await lecteur())

    async def faits(session, dataset):
        return []

    monkeypatch.setattr(copropriete, "copropriete_par_parcelle", lue)
    monkeypatch.setattr(copropriete, "lire_bloc", bloc)
    monkeypatch.setattr(copropriete, "faits_de_provenance", faits)
    monkeypatch.setattr(copropriete, "periode_de_source", lambda faits: None)
    theme = await copropriete.lire(_contexte())
    assert appels == [True]
    assert theme.details["registres"][0]["syndic"]["nom"] == "Cabinet Exemple"

    class Session:
        def __init__(self):
            self.sql = []

        async def execute(self, sql, parametres):
            self.sql.append(str(sql))
            return []

    session = Session()
    await queries.copropriete_par_parcelle(session, "44109000AB0001")
    await queries.copropriete_par_parcelle(session, "44109000AB0001", avec_gestion=True)
    assert "nom_syndic" not in session.sql[0]
    assert "nom_syndic" in session.sql[1]


# --- DPE : interdictions de location ------------------------------------------------


def test_each_step_of_the_ban_counts_the_documents_of_its_class():
    from datetime import date

    from app.rapport.themes.dpe import interdictions_location

    bloc = interdictions_location({"G": 2, "F": 1, "D": 4}, date(2026, 10, 30), "44109")
    assert bloc["etat"] == "ok" and bloc["unite"] == "documents_dpe"
    assert bloc["portee"] == "bail_signe_renouvele_ou_reconduit"
    assert bloc["etapes"] == [
        {
            "classe": "G",
            "a_partir_du": "2025-01-01",
            "en_vigueur": True,
            "documents": 2,
        },
        {
            "classe": "F",
            "a_partir_du": "2028-01-01",
            "en_vigueur": False,
            "documents": 1,
        },
        {
            "classe": "E",
            "a_partir_du": "2034-01-01",
            "en_vigueur": False,
            "documents": 0,
        },
    ]


def test_a_step_is_in_force_from_its_first_day():
    from datetime import date

    from app.rapport.themes.dpe import interdictions_location

    bloc = interdictions_location({}, date(2028, 1, 1), "44109")
    assert [e["en_vigueur"] for e in bloc["etapes"]] == [True, True, False]


@pytest.mark.parametrize("code", [None, ""])
def test_an_unknown_commune_has_no_calendar(code):
    from datetime import date

    from app.rapport.themes.dpe import interdictions_location

    assert interdictions_location({"G": 1}, date(2026, 10, 30), code) == {
        "etat": "hors_couverture",
        "motif": "commune_indeterminee",
    }


@pytest.mark.parametrize("code", ["97411", "97611", "97105"])
def test_the_overseas_calendar_is_not_served(code):
    from datetime import date

    from app.rapport.themes.dpe import interdictions_location

    assert interdictions_location({"G": 1}, date(2026, 10, 30), code) == {
        "etat": "hors_couverture",
        "motif": "calendrier_outre_mer_non_servi",
    }
