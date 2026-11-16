"""« DPE » : the energy performance documents declared at the address, condensed.

Granularity: address. The population is the one of the address page
(``app.dpe.queries``: documents whose address typed by the diagnostician is
this BAN key, retired documents excluded, D-109). It is attached to the
address, never to a building or a dwelling: no proximity, no nearest
building, no « only document of the key » shortcut (CONTEXT, « Invariants »).

Condensed: the count, the class distribution, the dominant class (none on a
tie), the range, and the most recent document by the rule of the address
page (:func:`app.adresse.documents.choisir_plus_recent`). The numbers of the
documents stay in the folded details.

Several buildings: at least two distinct RNB buildings named by the documents
(``id_rnb``), or at least two buildings the RNB itself links to the BAN key
(``rnb_liens``). The row says it, and the confidence drops to
``incertaine``: the documents may not concern the dwelling one looks at.
A single building is established only by the documents themselves: every
document names the same RNB building. The RNB links of the BAN key can only
reveal several buildings, never establish one (never on the BAN key alone).
An outage of the RNB links is said (``pannes_partielles``), never read as
« one building ».

Rental bans (04.7-08): the steps of the decency criterion (loi Climat et
résilience, art. 160; service-public.gouv.fr F2042): a lease signed,
renewed or tacitly extended from 2025 excludes G, from 2028 F, from 2034 E.
Each step counts the documents of its class, never dwellings, and says
whether it is in force on the date of the report. The overseas calendar
differs and is not served (out of coverage).

Tone rule: vigilance when the dominant class is F or G, or when the documents
come from several buildings; rassurant when the dominant class is A, B or C
and a single building is established; neutre otherwise, and when no document
is declared.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from app.adresse.blocs import faits_de_provenance, lire_bloc, periode_de_source
from app.adresse.documents import (
    CANDIDATS_MAX,
    choisir_plus_recent,
    date_import_dpe,
    departement_de_cle,
)
from app.adresse.lieu import _RNB_DE_LA_CLE
from app.dpe import queries
from app.rapport.schemas import Chiffre, Rattachement, Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sans_reponse,
    source,
    theme,
)

CLASSES = "ABCDEFG"
ENTITE = "adresse_saisie_diagnostiqueur"
# Only the ADEME dataset opened in July 2021 is imported. The documents of
# the former one all expired on 2024-12-31 at the latest (décret 2020-1610):
# a zero says since when, never « no DPE was ever made ».
VERDICT_AUCUN = (
    "Aucun DPE depuis juillet 2021 à cette adresse ; "
    "les diagnostics plus anciens ne sont plus valables."
)

# Buildings the documents of the population name (same population as the
# count and the distribution).
_BATIMENTS_DES_DOCUMENTS = text(f"""
    SELECT count(DISTINCT d.id_rnb) AS batiments_rnb,
           count(*) FILTER (WHERE d.id_rnb IS NULL) AS sans_rnb
    {queries.DOCUMENTS_ADRESSE_POPULATION_SQL}
""")


@dataclass(frozen=True)
class Batiments:
    """Buildings behind the documents: named by them, linked by the RNB."""

    rnb_des_documents: int
    documents_sans_rnb: int
    liens_rnb_de_la_cle: int | None
    liens_en_panne: bool = False

    @property
    def nombre(self) -> int:
        return max(self.rnb_des_documents, self.liens_rnb_de_la_cle or 0)

    @property
    def plusieurs(self) -> bool:
        return self.nombre >= 2

    @property
    def unique_etabli(self) -> bool:
        if self.plusieurs or self.liens_en_panne:
            return False
        return self.rnb_des_documents == 1 and self.documents_sans_rnb == 0


def classe_dominante(repartition: dict[str, int]) -> str | None:
    """The most frequent known class; None without class or on a tie."""
    connues = {c: repartition.get(c, 0) for c in CLASSES if repartition.get(c, 0) > 0}
    if not connues:
        return None
    plus = max(connues.values())
    premieres = [c for c, n in connues.items() if n == plus]
    return premieres[0] if len(premieres) == 1 else None


def plage(repartition: dict[str, int]) -> tuple[str, str] | None:
    connues = [c for c in CLASSES if repartition.get(c, 0) > 0]
    return (connues[0], connues[-1]) if connues else None


def ton_dpe(dominante: str | None, plusieurs: bool, unique_etabli: bool) -> Ton:
    if plusieurs or dominante in ("F", "G"):
        return "vigilance"
    if dominante in ("A", "B", "C") and unique_etabli:
        return "rassurant"
    return "neutre"


def _verdict(total: int, dominante: str | None, bornes, batiments: Batiments) -> str:
    if bornes is None:
        classes = "sans classe lisible"
    elif bornes[0] == bornes[1]:
        classes = f"classe {bornes[0]}"
    else:
        classes = f"classes {bornes[0]} à {bornes[1]}"
    if batiments.plusieurs:
        return (
            f"DPE de plusieurs bâtiments à cette adresse : {total} documents, "
            f"{classes}."
        )
    if total == 1:
        return f"1 DPE déclaré à cette adresse, {classes}."
    if dominante is not None:
        return (
            f"{total} DPE déclarés à cette adresse, classe {dominante} "
            "la plus fréquente"
            f" ({classes})."
        )
    return f"{total} DPE déclarés à cette adresse, {classes}."


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["dpe"]
    if hebergement.etat != "heberge":
        return sans_reponse(THEME, "hors_couverture", hebergement.motif)
    session = contexte.session
    ban_id = contexte.identite.ban_id
    departement = departement_de_cle(ban_id)
    parametres = {"ban_id": ban_id, "department": departement}

    async def lecteur():
        agregat = await queries.dpe_documents_adresse_agregat(
            session, ban_id=ban_id, department=departement
        )
        candidats, tronque = await queries.dpe_documents_adresse_candidats(
            session, ban_id=ban_id, department=departement, limit=CANDIDATS_MAX
        )
        batiments = (await session.execute(_BATIMENTS_DES_DOCUMENTS, parametres)).one()
        selection = choisir_plus_recent(candidats, contexte.as_of, complete=not tronque)
        return agregat, selection, batiments, await date_import_dpe(session)

    bloc = await lire_bloc(session, ["dpe"], lecteur)
    if bloc.en_panne or bloc.valeur is None:
        return sans_reponse(THEME, "panne", bloc.panne)
    agregat, selection, lignes_batiments, date_import = bloc.valeur
    liens, periode_rnb, liens_en_panne = await _liens_rnb(contexte)
    batiments = Batiments(
        rnb_des_documents=int(lignes_batiments.batiments_rnb),
        documents_sans_rnb=int(lignes_batiments.sans_rnb),
        liens_rnb_de_la_cle=liens,
        liens_en_panne=liens_en_panne,
    )
    millesime = date_import.isoformat() if date_import is not None else None
    sources = [source("dpe", millesime)]
    if liens is not None or liens_en_panne:
        # The links are read in the imported RNB export, not live.
        sources.append(source("rnb", periode_rnb, mode="import"))
    pannes = ["rnb"] if liens_en_panne else []
    total = agregat.total
    repartition = dict(agregat.distribution)
    if batiments.plusieurs:
        rattachement = Rattachement(
            entite=ENTITE,
            confiance="incertaine",
            candidats=batiments.nombre,
            motif="dpe_plusieurs_batiments"
            if batiments.rnb_des_documents >= 2
            else "plusieurs_batiments_rnb_a_l_adresse",
        )
    elif liens_en_panne:
        rattachement = Rattachement(
            entite=ENTITE, confiance="incertaine", motif="liens_rnb_indisponibles"
        )
    else:
        rattachement = Rattachement(entite=ENTITE, confiance="probable")
    details = {
        "repartition": repartition,
        "interdictions_location": interdictions_location(
            repartition, contexte.as_of, contexte.identite.commune.code_insee
        ),
        "documents_appartement": agregat.apartment_document_count,
        "plus_recent": selection.model_dump(mode="json"),
        "batiments": {
            "rnb_des_documents": batiments.rnb_des_documents,
            "documents_sans_rnb": batiments.documents_sans_rnb,
            "liens_rnb_de_la_cle": batiments.liens_rnb_de_la_cle,
            "un_seul_batiment_etabli": batiments.unique_etabli,
        },
    }
    if total == 0:
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict=VERDICT_AUCUN,
            details=details,
            rattachement=rattachement,
            sources=sources,
            pannes_partielles=pannes,
        )
    dominante = classe_dominante(repartition)
    bornes = plage(repartition)
    chiffres = [
        Chiffre(
            id="effectif",
            libelle="DPE déclarés",
            valeur=total,
            unite="count",
            millesime=millesime,
        )
    ]
    if dominante is not None:
        chiffres.append(
            Chiffre(
                id="classe_dominante",
                libelle="Classe la plus fréquente",
                valeur=dominante,
                unite="classe_dpe",
                millesime=millesime,
            )
        )
    if bornes is not None:
        chiffres.append(
            Chiffre(
                id="plage",
                libelle="Classes",
                valeur=f"{bornes[0]}\u2013{bornes[1]}"
                if bornes[0] != bornes[1]
                else bornes[0],
                unite="classe_dpe",
                millesime=millesime,
            )
        )
    return theme(
        THEME,
        etat="ok",
        ton=ton_dpe(dominante, batiments.plusieurs, batiments.unique_etabli),
        verdict=_verdict(total, dominante, bornes, batiments),
        chiffres=chiffres,
        details=details,
        rattachement=rattachement,
        sources=sources,
        pannes_partielles=pannes,
    )


# Steps of the decency criterion in metropolitan France: from this date, a
# lease signed, renewed or tacitly extended excludes this class.
ETAPES_DECENCE = (
    (date(2025, 1, 1), "G"),
    (date(2028, 1, 1), "F"),
    (date(2034, 1, 1), "E"),
)


def interdictions_location(
    repartition: dict[str, int], jour: date, code_commune: str | None
) -> dict:
    """The rental ban steps, each with the count of the documents of its class."""
    if not code_commune:
        return {"etat": "hors_couverture", "motif": "commune_indeterminee"}
    if code_commune.startswith("97"):
        return {"etat": "hors_couverture", "motif": "calendrier_outre_mer_non_servi"}
    return {
        "etat": "ok",
        "unite": "documents_dpe",
        # A lease signed, renewed or tacitly extended from the date, never
        # the lease in progress.
        "portee": "bail_signe_renouvele_ou_reconduit",
        "etapes": [
            {
                "classe": classe,
                "a_partir_du": debut.isoformat(),
                "en_vigueur": jour >= debut,
                "documents": int(repartition.get(classe, 0)),
            }
            for debut, classe in ETAPES_DECENCE
        ],
        # In a co-ownership, works voted but not carried out limit what a
        # judge can order: said, never assessed.
        "exception_copropriete": True,
    }


async def _liens_rnb(
    contexte: ContexteRapport,
) -> tuple[int | None, str | None, bool]:
    """Buildings the RNB links to the BAN key (two are enough), the
    millésime of the links export and whether that read failed;
    ``(None, None, False)`` when the RNB is not hosted."""
    if contexte.hosting.hostings["rnb"].etat != "heberge":
        return None, None, False
    session = contexte.session
    ban_id = contexte.identite.ban_id

    async def lecteur() -> tuple[int, str | None]:
        result = await session.execute(_RNB_DE_LA_CLE, {"ban_id": ban_id})
        liens = len(result.all())
        return liens, periode_de_source(await faits_de_provenance(session, "rnb_liens"))

    bloc = await lire_bloc(session, ["rnb"], lecteur)
    if bloc.en_panne or bloc.valeur is None:
        return None, None, True
    liens, periode = bloc.valeur
    return liens, periode, False


THEME = DefinitionTheme(
    id="dpe",
    section="batiment",
    ordre=10,
    titre="DPE",
    granularite=granularite("adresse"),
    sources=("dpe", "rnb"),
    lire=lire,
    rattachement_entite=ENTITE,
)
