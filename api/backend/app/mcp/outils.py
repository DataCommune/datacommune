"""The six tools of the MCP server (D-07): /v1 replayed, values projected.

Each tool replays one existing /v1 route through ``canal.lire_v1`` and projects
its values with ``contrat.mesure``; nothing here reads data or computes a
figure. A /v1 refusal (404, 422, 429, 503) is a tool error carrying its status,
never an empty result.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.requests import Request

from app.adresse.schemas import (
    ColonneAdresse,
    CorrespondanceNumeroRecherche,
    CouvertureAdresse,
    DesignationEcartee,
    HebergementBanRecherche,
    IdentiteAdresse,
    MotifNonCalculAdresse,
    ResultatRechercheAdresse,
)
from app.common.schemas import DatasetHosting, DatasetProvenance
from app.commune.schemas import (
    COMMUNE_FIELD_SOURCES,
    CodeHorsCouverture,
    Ecart,
    MotifNonCalcul,
    ResultatCommuneSynthese,
    commune_field_id,
)
from app.mcp.canal import ReponseV1, lire_v1
from app.mcp.contrat import (
    MOTIF_PANNE,
    MOTIFS_DU_MANQUE,
    REGLE_DE_LECTURE,
    Ecartee,
    Echelle,
    ElementMcp,
    EtatMcp,
    Mesure,
    SourceMcp,
    ValeurEcartee,
    contrat_du_champ,
    echelle_du_rattachement,
    mesure,
    source_du_catalogue,
)

# Same syntax as the /v1 path parameter (app.adresse.router.BAN_ID_PATTERN).
_CLE_BAN = Annotated[
    str,
    StringConstraints(
        max_length=40,
        pattern=r"^[0-9][0-9AB][0-9]{3}_[0-9A-Za-z]{4,6}(_[0-9]{5}(_[^\s/\\]{1,10})?)?$",
    ),
]
_CODE_INSEE = Annotated[
    str, StringConstraints(pattern=r"^(?:\d{5}|2[AB]\d{3})$", to_upper=True)
]


class OutilError(Exception):
    """A tool call that cannot return a result; its text is shown to the agent."""


# --- Inputs ------------------------------------------------------------------


class _Entree(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntreeChercherAdresse(_Entree):
    saisie: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "Adresse ou début d'adresse, en clair (« 12 rue des Lilas Nantes »)."
        ),
    )
    departement: str | None = Field(
        default=None,
        max_length=3,
        description="Code de département pour restreindre la recherche (44, 2A, 974).",
    )
    limite: int = Field(default=8, ge=1, le=8, description="Résultats au plus.")


class EntreeLireAdresse(_Entree):
    cle_ban: _CLE_BAN = Field(
        description="Clé de l'adresse dans la Base Adresse Nationale, rendue par "
        "chercher_adresse (ex. 44109_1234_00012)."
    )


class EntreeComparerAdresses(_Entree):
    cles_ban: list[_CLE_BAN] = Field(
        min_length=2,
        max_length=4,
        description="2 à 4 clés BAN ; la première est la référence des écarts.",
    )


class EntreeLireCommune(_Entree):
    code_insee: _CODE_INSEE = Field(
        description="Code INSEE de la commune ou d'un arrondissement de Paris, "
        "Lyon, Marseille (ex. 44109, 75112)."
    )


class EntreeComparerCommunes(_Entree):
    codes_insee: list[_CODE_INSEE] = Field(
        min_length=2,
        max_length=4,
        description="2 à 4 codes INSEE distincts ; le premier est la référence.",
    )


class EntreeListerJeux(_Entree):
    pass


# --- Outputs -----------------------------------------------------------------


class _Sortie(BaseModel):
    lecture: str = Field(default=REGLE_DE_LECTURE, description="Règle de lecture.")


class SortieChercherAdresse(_Sortie):
    etat_recherche: Literal["aboutie", "trop_large"] = Field(
        description="trop_large : recherche arrêtée par son budget, aucun résultat "
        "et aucune absence établie ; préciser la commune ou le code postal."
    )
    resultats: list[ResultatRechercheAdresse]
    correspondance_numero: CorrespondanceNumeroRecherche | None = None
    couverture_ban: HebergementBanRecherche | None = None
    source: SourceMcp


class DpePlusRecent(BaseModel):
    """Most recent DPE declared at the address, as /v1 selects it."""

    etat: Literal["unique", "egalite", "indeterminee"]
    motif: str
    date: str | None = None
    numero: str | None = None
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None


class ThemeAbsent(BaseModel):
    """A theme of the place served without values, with its state."""

    theme: str
    titre: str
    etat: EtatMcp
    motif: str


class SortieLireAdresse(_Sortie):
    identite: IdentiteAdresse
    couverture: CouvertureAdresse
    mesures: list[Mesure]
    dpe_plus_recent: DpePlusRecent | None = None
    themes_absents: list[ThemeAbsent] = Field(default_factory=list)
    ecartees: list[Ecartee] = Field(default_factory=list)


class CelluleAdresse(Mesure):
    """One address's value for a compared row, and its verdict to column 1."""

    cle_ban: str
    ecart: Ecart | None = None
    motif_non_calcul: MotifNonCalculAdresse | None = None
    verdict: Literal["identique", "different"] | None = None


class LigneAdresses(BaseModel):
    groupe: str
    champ: str
    identiques: bool
    cellules: list[CelluleAdresse]


class SortieComparerAdresses(_Sortie):
    colonnes: list[ColonneAdresse]
    lignes: list[LigneAdresses]
    ids_ecartes: list[str] = Field(default_factory=list)
    designations_ecartees: list[DesignationEcartee] = Field(default_factory=list)
    ecartees: list[Ecartee] = Field(default_factory=list)


class CommuneMcp(BaseModel):
    code_insee: str
    nom: str | None = None
    code_departement: str | None = None
    type_zone: Literal["commune", "arrondissement"] | None = None
    commune_parent: str | None = None


class SortieLireCommune(_Sortie):
    commune: CommuneMcp
    etat: Literal["servie", "hors_couverture"]
    couverture: dict[str, DatasetHosting] = Field(default_factory=dict)
    mesures: list[Mesure] = Field(default_factory=list)
    ecartees: list[Ecartee] = Field(default_factory=list)


class CelluleCommune(Mesure):
    """One commune's value for a criterion, and its verdict to column 1."""

    code_insee: str
    ecart: Ecart | None = None
    motif_non_calcul: MotifNonCalcul | None = None


class CritereCommunes(BaseModel):
    cle: str
    champ: str
    identiques: bool
    cellules: list[CelluleCommune]


class ColonneCommune(BaseModel):
    code_insee: str
    nom: str
    departement: str
    hebergement: DatasetHosting
    type_zone: Literal["commune", "arrondissement"] | None = None


class SortieComparerCommunes(_Sortie):
    communes: list[ColonneCommune]
    criteres: list[CritereCommunes]
    codes_inconnus: list[str] = Field(default_factory=list)
    codes_hors_couverture: list[CodeHorsCouverture] = Field(default_factory=list)
    ecartees: list[Ecartee] = Field(default_factory=list)


class JeuDeDonnees(BaseModel):
    cle: str
    statut: str
    provenance: DatasetProvenance | None = None
    hebergement: DatasetHosting | None = None


class SortieListerJeux(BaseModel):
    jeux: list[JeuDeDonnees]


# --- /v1 replay ----------------------------------------------------------------


def _donnees(reponse: ReponseV1, route: str) -> Any:
    """The ``donnees`` of a 200, or the /v1 refusal as a tool error."""
    if reponse.statut == 200 and isinstance(reponse.corps, dict):
        return reponse.corps.get("donnees")
    corps = reponse.corps if isinstance(reponse.corps, dict) else {}
    titre = corps.get("title") or "Réponse inattendue"
    detail = corps.get("detail")
    texte = f"{route} a répondu {reponse.statut} : {titre}"
    if detail:
        texte += f". {detail}"
    if reponse.retry_after:
        texte += f" Réessayer dans {reponse.retry_after} s."
    raise OutilError(texte)


def _source_de_valeur(source: dict | None) -> SourceMcp | None:
    if not source:
        return None
    return source_du_catalogue(source.get("cle")) or SourceMcp.model_validate(source)


def _ajouter(
    liste: list,
    ecartees: list[Ecartee],
    champ: str,
    construire: Callable[..., Mesure],
    *arguments: Any,
) -> None:
    """Append the projected value, or name it in *ecartees* when set aside."""
    try:
        liste.append(construire(*arguments))
    except ValeurEcartee as ecart:
        ecartees.append(Ecartee(champ=champ, raison=ecart.raison))


def _mesure_de_valeur_lieu(valeur: dict) -> Mesure:
    """A ``ValeurLieu`` / ``ValeurTheme`` of an address page as a ``Mesure``."""
    champ = valeur["field_id"]
    elements = [ElementMcp.model_validate(e) for e in valeur.get("elements") or []]
    return mesure(
        champ=champ,
        etat_v1=valeur["etat"],
        valeur=valeur.get("valeur"),
        motif_v1=valeur.get("motif"),
        source=_source_de_valeur(valeur.get("source")),
        date=valeur.get("periode"),
        echelle=echelle_du_rattachement(valeur.get("rattachement"), champ),
        unite=valeur.get("unite"),
        rayon_m=valeur.get("rayon_m"),
        elements=elements or None,
        liste_incomplete=bool(valeur.get("liste_incomplete")),
    )


def _etat_d_absence(etat_v1: str, motif: str | None) -> tuple[EtatMcp, str]:
    """State and reason of a whole theme served without values."""
    if etat_v1 == "out_of_coverage":
        return "hors_couverture", motif or "non_precise"
    if etat_v1 == "temporary_error":
        return "panne", MOTIF_PANNE
    return "manquant", MOTIFS_DU_MANQUE.get(etat_v1, etat_v1)


# --- Address tools -------------------------------------------------------------


async def chercher_adresse(
    request: Request, entree: EntreeChercherAdresse
) -> SortieChercherAdresse:
    params = {"q": entree.saisie, "limite": str(entree.limite)}
    if entree.departement:
        params["departement"] = entree.departement
    donnees = _donnees(
        await lire_v1(request, "/v1/adresses/recherche", params),
        "/v1/adresses/recherche",
    )
    source = source_du_catalogue("ban")
    if source is None:
        raise OutilError("Source BAN absente du catalogue.")
    return SortieChercherAdresse(
        etat_recherche=donnees.get("etat", "aboutie"),
        resultats=donnees["resultats"],
        correspondance_numero=donnees.get("correspondance_numero"),
        couverture_ban=(donnees.get("hebergement") or {}).get("ban"),
        source=source,
    )


def _projeter_reperes(reperes: dict, mesures: list, ecartees: list) -> None:
    valeurs = [
        reperes["dpe"],
        reperes["ventes"]["nombre"],
        reperes["ventes"]["derniere"],
        reperes["argiles"],
        reperes["reference_commune"]["maisons"],
        reperes["reference_commune"]["appartements"],
    ]
    for valeur in valeurs:
        _ajouter(mesures, ecartees, valeur["field_id"], _mesure_de_valeur_lieu, valeur)


def _dpe_plus_recent(reperes: dict | None) -> DpePlusRecent | None:
    selection = (reperes or {}).get("dpe", {}).get("selection")
    if not selection:
        return None
    document = selection.get("document") or {}
    return DpePlusRecent(
        etat=selection["etat"],
        motif=selection["motif"],
        date=selection.get("date"),
        numero=document.get("numero"),
        etiquette_dpe=document.get("etiquette_dpe"),
        etiquette_ges=document.get("etiquette_ges"),
    )


async def lire_adresse(
    request: Request, entree: EntreeLireAdresse
) -> SortieLireAdresse:
    route = f"/v1/adresse/{quote(entree.cle_ban, safe='')}"
    fiche = _donnees(await lire_v1(request, route), "/v1/adresse")
    mesures: list[Mesure] = []
    ecartees: list[Ecartee] = []
    themes_absents: list[ThemeAbsent] = []
    if fiche.get("reperes"):
        _projeter_reperes(fiche["reperes"], mesures, ecartees)
    for theme in fiche.get("themes") or []:
        if not theme.get("applicable", True):
            continue
        if theme["etat"] != "value":
            etat, motif = _etat_d_absence(theme["etat"], theme.get("motif"))
            themes_absents.append(
                ThemeAbsent(
                    theme=theme["cle"], titre=theme["titre"], etat=etat, motif=motif
                )
            )
        for groupe in theme.get("groupes") or []:
            for valeur in groupe.get("champs") or []:
                champ = valeur["field_id"]
                _ajouter(mesures, ecartees, champ, _mesure_de_valeur_lieu, valeur)
    return SortieLireAdresse(
        identite=fiche["identite"],
        couverture=fiche["couverture"],
        mesures=mesures,
        dpe_plus_recent=_dpe_plus_recent(fiche.get("reperes")),
        themes_absents=themes_absents,
        ecartees=ecartees,
    )


def _cellule_adresse(cellule: dict) -> CelluleAdresse:
    return CelluleAdresse(
        **_mesure_de_valeur_lieu(cellule).model_dump(),
        cle_ban=cellule["ban_id"],
        ecart=cellule.get("ecart"),
        motif_non_calcul=cellule.get("motif_non_calcul"),
        verdict=cellule.get("verdict"),
    )


async def comparer_adresses(
    request: Request, entree: EntreeComparerAdresses
) -> SortieComparerAdresses:
    donnees = _donnees(
        await lire_v1(
            request, "/v1/adresses/comparaison", {"ids": ",".join(entree.cles_ban)}
        ),
        "/v1/adresses/comparaison",
    )
    lignes: list[LigneAdresses] = []
    ecartees: list[Ecartee] = []
    for groupe in donnees["groupes"]:
        for ligne in groupe["lignes"]:
            cellules: list[CelluleAdresse] = []
            for cellule in ligne["cellules"]:
                champ = ligne["field_id"]
                _ajouter(cellules, ecartees, champ, _cellule_adresse, cellule)
            lignes.append(
                LigneAdresses(
                    groupe=groupe["cle"],
                    champ=ligne["field_id"],
                    identiques=ligne["identiques"],
                    cellules=cellules,
                )
            )
    return SortieComparerAdresses(
        colonnes=donnees["colonnes"],
        lignes=lignes,
        ids_ecartes=donnees.get("ids_ecartes") or [],
        designations_ecartees=donnees.get("designations_ecartees") or [],
        ecartees=ecartees,
    )


# --- Commune tools -------------------------------------------------------------

# Column carrying the vintage of each source's flat columns in a /v1/commune row.
_COLONNE_DE_DATE = {
    "demographie": "annee_demographie",
    "revenus": "annee_revenus",
    "vacance": "annee_vacance",
    "fiscalite": "annee_fiscalite",
    "loyers": "annee_loyers",
    "equipements": "annee_equipements",
    "dvf": "fenetre_mediane_dvf",
}


def _colonnes_projetees() -> dict[str, str]:
    """Flat value columns of the synthesis with a contract entry (D-08).

    Vintage columns (``*.annee``) are dates of the others, not values; nested
    blocks (DVF and DPE indicators, price trend, rent objects) have no entry
    of their own and are left to /v1/commune.
    """
    colonnes = {}
    for colonne in ResultatCommuneSynthese.model_fields:
        champ = commune_field_id((colonne,))
        if champ and contrat_du_champ(champ) and not champ.endswith(".annee"):
            colonnes[colonne] = champ
    return colonnes


# Flat column of /v1/commune -> its fiche-fields id, in the order of the model.
COLONNES_COMMUNE = _colonnes_projetees()


def _provenances(meta: dict | None) -> dict[str, dict]:
    sources = (meta or {}).get("source")
    if not isinstance(sources, list):
        sources = [sources] if sources else []
    return {s["dataset"]: s for s in sources if isinstance(s, dict) and "dataset" in s}


def _date_de_provenance(provenance: dict | None) -> str | None:
    if not provenance:
        return None
    periode = provenance.get("periode") or {}
    if periode.get("statut") != "inconnue":
        if periode.get("label"):
            return periode["label"]
        if periode.get("debut") and periode.get("fin"):
            return f"{periode['debut']} → {periode['fin']}"
    importation = provenance.get("derniere_importation_reussie")
    return f"import du {importation[:10]}" if importation else None


def _date_commune(source: str, ligne: dict, provenances: dict) -> str | None:
    colonne = _COLONNE_DE_DATE.get(source)
    if colonne and ligne.get(colonne) is not None:
        return str(ligne[colonne])
    return _date_de_provenance(provenances.get(source))


def _echelle_commune(ligne: dict, colonne: str) -> Echelle:
    if colonne in (ligne.get("heritages") or {}):
        return "commune"
    return "arrondissement" if ligne.get("type_zone") == "arrondissement" else "commune"


def _mesure_de_colonne(ligne: dict, colonne: str, provenances: dict) -> Mesure:
    """A flat column of a /v1/commune row; its absence state is in ``etats``."""
    source = COMMUNE_FIELD_SOURCES[colonne]
    valeur = ligne.get(colonne)
    absence = (ligne.get("etats") or {}).get(colonne) or {}
    projetee = mesure(
        champ=COLONNES_COMMUNE[colonne],
        etat_v1="value" if valeur is not None else absence.get("etat"),
        valeur=valeur,
        motif_v1=absence.get("motif"),
        source=source_du_catalogue(source),
        date=_date_commune(source, ligne, provenances),
        echelle=_echelle_commune(ligne, colonne),
    )
    return projetee.model_copy(update={"cle": colonne})


def _mesures_commune(ligne: dict, provenances: dict) -> tuple[list, list]:
    mesures: list[Mesure] = []
    ecartees: list[Ecartee] = []
    for colonne, champ in COLONNES_COMMUNE.items():
        _ajouter(
            mesures, ecartees, champ, _mesure_de_colonne, ligne, colonne, provenances
        )
    return mesures, ecartees


async def lire_commune(
    request: Request, entree: EntreeLireCommune
) -> SortieLireCommune:
    reponse = await lire_v1(request, "/v1/commune", {"code_insee": entree.code_insee})
    lignes = _donnees(reponse, "/v1/commune")
    meta = reponse.corps.get("meta") or {}
    if not lignes:
        return SortieLireCommune(
            commune=CommuneMcp(code_insee=entree.code_insee),
            etat="hors_couverture",
            couverture=meta.get("hebergement") or {},
        )
    ligne = lignes[0]
    mesures, ecartees = _mesures_commune(ligne, _provenances(meta))
    return SortieLireCommune(
        commune=CommuneMcp(
            code_insee=ligne["code_insee"],
            nom=ligne.get("nom_commune"),
            code_departement=ligne.get("code_departement"),
            type_zone=ligne.get("type_zone"),
            commune_parent=ligne.get("commune_parent"),
        ),
        etat="servie",
        couverture=ligne.get("couverture") or {},
        mesures=mesures,
        ecartees=ecartees,
    )


def _cellule_commune(
    critere: dict, cellule: dict, colonne: dict, provenances: dict
) -> CelluleCommune:
    champ = critere["field_id"]
    source = champ.split(".", 1)[0]
    synthese = colonne.get("synthese") or {}
    absence = (synthese.get("etats") or {}).get(critere["cle"]) or {}
    motif = absence.get("motif")
    if cellule.get("etat") == "out_of_coverage" and motif is None:
        hebergement = (synthese.get("couverture") or {}).get(source) or colonne.get(
            "hebergement", {}
        )
        motif = hebergement.get("motif")
    echelle = _echelle_commune(synthese, critere["cle"]) if synthese else "commune"
    base = mesure(
        champ=champ,
        etat_v1=cellule.get("etat"),
        valeur=cellule.get("valeur"),
        motif_v1=motif,
        source=source_du_catalogue(source),
        date=cellule.get("periode") or _date_de_provenance(provenances.get(source)),
        echelle=echelle,
        unite=critere.get("unite"),
    )
    return CelluleCommune(
        **base.model_dump(),
        code_insee=cellule["code_insee"],
        ecart=cellule.get("ecart"),
        motif_non_calcul=cellule.get("motif_non_calcul"),
    )


async def comparer_communes(
    request: Request, entree: EntreeComparerCommunes
) -> SortieComparerCommunes:
    reponse = await lire_v1(
        request, "/v1/communes/comparaison", {"codes": ",".join(entree.codes_insee)}
    )
    donnees = _donnees(reponse, "/v1/communes/comparaison")
    provenances = _provenances(reponse.corps.get("meta"))
    colonnes = {c["code_insee"]: c for c in donnees["communes"]}
    criteres: list[CritereCommunes] = []
    ecartees: list[Ecartee] = []
    for critere in donnees["criteres"]:
        cellules: list[CelluleCommune] = []
        for cellule in critere["cellules"]:
            colonne = colonnes.get(cellule["code_insee"], {})
            _ajouter(
                cellules,
                ecartees,
                critere["field_id"],
                _cellule_commune,
                critere,
                cellule,
                colonne,
                provenances,
            )
        criteres.append(
            CritereCommunes(
                cle=critere["cle"],
                champ=critere["field_id"],
                identiques=critere["identiques"],
                cellules=cellules,
            )
        )
    return SortieComparerCommunes(
        communes=[
            ColonneCommune(
                code_insee=c["code_insee"],
                nom=c["nom"],
                departement=c["departement"],
                hebergement=c["hebergement"],
                type_zone=(c.get("synthese") or {}).get("type_zone"),
            )
            for c in donnees["communes"]
        ],
        criteres=criteres,
        codes_inconnus=donnees.get("codes_inconnus") or [],
        codes_hors_couverture=donnees.get("codes_hors_couverture") or [],
        ecartees=ecartees,
    )


async def lister_jeux_de_donnees(
    request: Request, entree: EntreeListerJeux
) -> SortieListerJeux:
    del entree
    jeux = _donnees(await lire_v1(request, "/v1/datasets"), "/v1/datasets")
    return SortieListerJeux(
        jeux=[
            JeuDeDonnees(
                cle=jeu["nom"],
                statut=jeu["statut"],
                provenance=jeu.get("provenance"),
                hebergement=jeu.get("hebergement"),
            )
            for jeu in jeux
        ]
    )


# --- Registry -----------------------------------------------------------------


@dataclass(frozen=True)
class Outil:
    nom: str
    titre: str
    description: str
    entree: type[BaseModel]
    sortie: type[BaseModel]
    executer: Callable[[Request, Any], Awaitable[BaseModel]]

    def definition(self) -> dict:
        """The ``tools/list`` entry: schemas generated from the models."""
        return {
            "name": self.nom,
            "title": self.titre,
            "description": self.description,
            "inputSchema": self.entree.model_json_schema(),
            "outputSchema": self.sortie.model_json_schema(),
            "annotations": {
                "title": self.titre,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


_COMMUN = (
    " Chaque valeur est une mesure : état (servi, zero, manquant, "
    "hors_couverture, panne), source, date et échelle ; seules servi et zero portent "
    "une valeur. Données publiques ouvertes, lecture seule."
)

OUTILS: dict[str, Outil] = {
    outil.nom: outil
    for outil in (
        Outil(
            "chercher_adresse",
            "Chercher une adresse",
            "Trouve des adresses de la Base Adresse Nationale à partir d'une saisie "
            "libre et rend leur clé BAN, à passer ensuite à lire_adresse ou "
            "comparer_adresses. Aucune coordonnée n'est rendue. Une recherche "
            "« trop_large » n'établit aucune absence.",
            EntreeChercherAdresse,
            SortieChercherAdresse,
            chercher_adresse,
        ),
        Outil(
            "lire_adresse",
            "Lire la fiche d'une adresse",
            "Rend la fiche d'une adresse par sa clé BAN : identité, parcelle du "
            "point, DPE déclarés à l'adresse, ventes de la parcelle, exposition aux "
            "argiles, prix médians de la commune en référence, puis bâtiment, "
            "risques, urbanisme, réseaux et transports. Un document DPE ou une "
            "vente n'est jamais rattaché à un logement." + _COMMUN,
            EntreeLireAdresse,
            SortieLireAdresse,
            lire_adresse,
        ),
        Outil(
            "comparer_adresses",
            "Comparer des adresses",
            "Compare 2 à 4 adresses ligne par ligne, avec pour chaque adresse après "
            "la première l'écart à la première, ou le motif pour lequel il n'est "
            "pas calculé (périodes ou méthodes différentes, donnée absente…)."
            + _COMMUN,
            EntreeComparerAdresses,
            SortieComparerAdresses,
            comparer_adresses,
        ),
        Outil(
            "lire_commune",
            "Lire une commune",
            "Rend les chiffres clés d'une commune ou d'un arrondissement de Paris, "
            "Lyon, Marseille : population, revenus, prix et ventes immobilières, "
            "loyers, DPE, vacance, fiscalité locale, équipements, fibre. Les "
            "indicateurs détaillés (quantiles, séries annuelles) restent sur "
            "l'API /v1/commune." + _COMMUN,
            EntreeLireCommune,
            SortieLireCommune,
            lire_commune,
        ),
        Outil(
            "comparer_communes",
            "Comparer des communes",
            "Compare 2 à 4 communes, grandes villes comprises, ou des arrondissements "
            "de Paris, Lyon, Marseille, valeur par valeur, avec l'écart à la "
            "première ou le motif pour lequel il n'est pas calculé. Aucun score ni "
            "classement. L'échelle intercommunale (métropole, EPCI) n'est pas "
            "servie : comparer les communes centres." + _COMMUN,
            EntreeComparerCommunes,
            SortieComparerCommunes,
            comparer_communes,
        ),
        Outil(
            "lister_jeux_de_donnees",
            "Lister les jeux de données",
            "Liste les jeux de données publics de cette instance : producteur, "
            "licence, période couverte, dernière importation, état de fraîcheur et "
            "territoires hébergés.",
            EntreeListerJeux,
            SortieListerJeux,
            lister_jeux_de_donnees,
        ),
    )
}
