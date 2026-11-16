"""Pydantic schemas for DVF property transaction endpoints.

Two independent contracts live here:

- :class:`ResultatDvf`, the mutation-grain transaction row published by the
  ``/v1/dvf`` endpoints (Phase 57, D-15);
- :class:`ReponseDvfAdresse` (C52, B1), the envelope of ``/v1/dvf/adresse``: a
  proximity search around one retained geocoded point, never an address history;
- :class:`DvfAttribution` (Phase 57, plan 12), the ATTRIBUTION contract: what
  may be said about the property whose cadastral identity was resolved. Its
  five states are closed and every impossible combination is rejected before
  serialization, so no caller can turn a neighbouring local, a mixed sale or a
  block sale into "the last sale of this property" (D-10/D-11/D-16/D-37).
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.schemas import ProblemDetail, ReponsePaginee
from app.etl_dvf.qualification import CLASSES_MUTATION, RAISONS_PRIX_M2


class ResultatDvf(BaseModel):
    """DVF property transaction result with spatial and financial data.

    Phase 57 (D-15): the endpoints now read the reconstructed
    ``mutations_grain`` (one row per ``id_mutation``, D-01) instead of the raw
    per-line ``mutations`` table. Every field below ``latitude`` is ADDITIVE --
    nothing above it is removed or retyped (D-15 literal). ``prix_m2`` is
    honest by construction: it is only ever non-null for a mono-local
    residential sale (or VEFA), and ``prix_m2_raison`` explains every other
    case from the closed :data:`app.etl_dvf.qualification.RAISONS_PRIX_M2`
    vocabulary -- never a free string.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id_mutation": "2023-1234",
                    "date_mutation": "2023-06-15",
                    "nature_mutation": "Vente",
                    "valeur_fonciere": 350000.0,
                    "type_local": "Appartement",
                    "surface_reelle_bati": 65,
                    "nombre_pieces_principales": 3,
                    "surface_terrain": None,
                    "prix_m2": 5385.0,
                    "adresse_numero": "12",
                    "adresse_nom_voie": "Rue de la Paix",
                    "code_postal": "75002",
                    "nom_commune": "Paris",
                    "id_parcelle": "75102000AB0001",
                    "code_type_local": "2",
                    "longitude": 2.3312,
                    "latitude": 48.8688,
                    "classe_mutation": "residentiel_appartement",
                    "prix_m2_raison": None,
                    "mutation_mono_local": True,
                    "nb_locaux_maison": 0,
                    "nb_locaux_appartement": 1,
                    "nb_locaux_commercial": 0,
                    "nb_lots_source": 1,
                    "nb_parcelles": 1,
                    "hors_stats_vefa": False,
                    "surface_bati_totale": 65,
                }
            ]
        }
    )

    id_mutation: str
    date_mutation: date
    nature_mutation: str | None = None
    valeur_fonciere: float | None = None
    type_local: str | None = None
    surface_reelle_bati: int | None = None
    nombre_pieces_principales: int | None = None
    surface_terrain: int | None = None
    prix_m2: float | None = None
    adresse_numero: str | None = None
    adresse_nom_voie: str | None = None
    code_postal: str | None = None
    nom_commune: str | None = None
    id_parcelle: str | None = None
    code_type_local: str | None = None
    code_nature_culture: str | None = None
    nature_culture: str | None = None
    code_nature_culture_speciale: str | None = None
    nature_culture_speciale: str | None = None
    longitude: float
    latitude: float

    # -- Additive qualification fields (Phase 57, D-15) -- every field below
    # is optional and defaults to None so a pre-Phase-57 consumer sees no
    # difference. Nothing above this line was removed or retyped.
    classe_mutation: str | None = None
    prix_m2_raison: str | None = None
    mutation_mono_local: bool | None = None
    # Kept as two separate counters (never merged into one "residentiel"
    # figure) -- same strict maison/appartement separation doctrine as D-10
    # ("une vente maison n'alimente jamais le KPI appartement et
    # réciproquement"), mirrored field-for-field from mutations_grain.
    nb_locaux_maison: int | None = None
    nb_locaux_appartement: int | None = None
    nb_locaux_commercial: int | None = None
    # `nombre_lots` is the source's PER-LOCAL metadata (D-02) -- never summed
    # or maxed across a mutation's lines (Pitfall 3 / DVF-02: it varies by
    # LINE, not by disposition, in the real geo-dvf file). The `_source`
    # suffix is deliberate: this is the raw DGFiP/geo-dvf figure of the one
    # attributable local (populated only for mono-local mutations, where a
    # single local unambiguously owns it), propagated as-is -- never a total
    # DataCommune computes itself. `None` when the mutation isn't mono-local
    # (no single local to attribute it to).
    nb_lots_source: int | None = None
    nb_parcelles: int | None = None
    hors_stats_vefa: bool | None = None
    # Distinct name from `surface_reelle_bati` above for forward clarity, but
    # the SAME value at the mutation grain (Phase 57, Task 2): once the
    # endpoints read `mutations_grain` exclusively, there is no more
    # per-line surface to report -- `surface_reelle_bati` is populated with
    # the mutation's total surface too (semantics change on a kept field,
    # documented at the query layer in `app/dvf/queries.py`).
    surface_bati_totale: int | None = None

    @model_validator(mode="after")
    def _valide_classe_mutation(self) -> "ResultatDvf":
        if (
            self.classe_mutation is not None
            and self.classe_mutation not in CLASSES_MUTATION
        ):
            raise ValueError(
                f"classe_mutation {self.classe_mutation!r} n'appartient pas au "
                "vocabulaire ferme (app.etl_dvf.qualification.CLASSES_MUTATION)"
            )
        return self

    @model_validator(mode="after")
    def _valide_prix_m2_raison(self) -> "ResultatDvf":
        if (
            self.prix_m2_raison is not None
            and self.prix_m2_raison not in RAISONS_PRIX_M2
        ):
            raise ValueError(
                f"prix_m2_raison {self.prix_m2_raison!r} n'appartient pas au "
                "vocabulaire ferme (app.etl_dvf.qualification.RAISONS_PRIX_M2)"
            )
        if (self.prix_m2_raison is not None) != (self.prix_m2 is None):
            raise ValueError(
                "prix_m2_raison doit etre renseigne si et seulement si "
                "prix_m2 est nul (D-15) -- un payload incoherent (les deux "
                "presents, ou les deux absents) est inconstructible"
            )
        return self


# --- Phase 57 plan 12: attribution contract (D-10/D-11/D-13/D-16/D-37) -------

DvfAttributionStatus = Literal[
    "exacte",
    "compatible_parcelle",
    "non_attribuable",
    "absente",
    "error",
]

# Version of the decision rules that produced an attribution. Published with
# every decision (D-37: "candidats, règle/version et preuves restent
# explicables") so a payload read months later can be traced back to the rules
# that built it. Bump it whenever the policy of ``app.dvf.attribution`` changes
# a published outcome -- as plan 57-18 did by making the DECLARED type of the
# consulted dwelling an input of the decision (D-41), and as 57-17/D-47 does by
# splitting the VEFA absence in two: a VEFA carrying a surface is no longer
# answered « ce n'est pas une vente » (``nature_hors_vente``, which was false)
# but ``vente_sur_plan``. The outcome published on such a parcel changes, so
# the version does too. The rules are no longer the ones 57-12 shipped.
DVF_ATTRIBUTION_RULE_VERSION = "57-17/D-47"

# Closed set of short, constant public reasons. Same doctrine as
# ``app.resolve.schemas.RESOLUTION_RAISONS`` and
# ``app.dpe.schemas.DPE_ATTRIBUTION_RAISONS``: never an upstream exception,
# never SQL, never an invented word of truth. Every absence is explicable in
# ONE line (D-16) -- that is the whole point of keeping the vocabulary closed.
DVF_ATTRIBUTION_RAISONS: dict[str, str] = {
    "aucune_mutation": "Aucune mutation DVF observée sur cette parcelle.",
    "derniere_vente_mixte": (
        "La mutation la plus récente mêle logement et local commercial : "
        "elle n'est pas attribuable à ce bien."
    ),
    "derniere_vente_commerciale": (
        "La mutation la plus récente ne porte que des locaux commerciaux."
    ),
    "vente_ensemble": (
        "La mutation la plus récente porte plusieurs logements vendus "
        "ensemble, à prix global."
    ),
    "type_incompatible": (
        "La mutation la plus récente porte un type de bien différent de celui consulté."
    ),
    "classe_non_residentielle": (
        "La mutation la plus récente n'est pas la vente d'un logement unique."
    ),
    "vefa_sans_surface": (
        "Vente en l'état futur d'achèvement sans surface : aucun prix au m² "
        "n'est calculable."
    ),
    # D-47: a VEFA carrying a surface used to fall back on `nature_hors_vente`
    # -- « une transaction qui n'est pas une vente ». That was false: a VEFA IS
    # a sale, simply of a different market, and it is never attributed to the
    # consulted dwelling (D-12/D-13). The reason now says the true thing.
    "vente_sur_plan": (
        "La mutation la plus récente est une vente sur plan (VEFA) : elle n'est "
        "pas attribuable à ce bien."
    ),
    "nature_hors_vente": (
        "La mutation la plus récente n'est pas une vente au sens du périmètre publié."
    ),
    "type_bien_inconnu": (
        "Le type du bien consulté n'est pas résolu : aucune compatibilité "
        "ne peut être établie."
    ),
    "parcelle_indisponible": (
        "Aucune parcelle cadastrale résolue : aucune mutation n'a été cherchée."
    ),
    "entree_illisible": (
        "Données de mutation illisibles : aucune attribution n'a pu être décidée."
    ),
    "temporary_error": "Données DVF momentanément indisponibles.",
}

# Reasons each state may carry. The two positive states carry none: their
# payload IS the answer. The split between ``non_attribuable`` (a mutation
# exists but cannot be tied to this property), ``absente`` (nothing was found,
# or nothing was even looked up) and ``error`` (a technical failure) is what
# forbids reading a failure as "aucune vente ici" (D-16, DPE D-13 doctrine).
_RAISONS_PAR_ETAT: dict[str, frozenset[str]] = {
    "exacte": frozenset(),
    "compatible_parcelle": frozenset(),
    "non_attribuable": frozenset(
        {
            "derniere_vente_mixte",
            "derniere_vente_commerciale",
            "vente_ensemble",
            "type_incompatible",
            "classe_non_residentielle",
            "vefa_sans_surface",
            "vente_sur_plan",
            "nature_hors_vente",
            "type_bien_inconnu",
        }
    ),
    "absente": frozenset({"aucune_mutation", "parcelle_indisponible"}),
    "error": frozenset({"entree_illisible", "temporary_error"}),
}

# The ONLY proofs that promote a compatible mutation to ``exacte`` (D-37): an
# identified lot or an identified local inside the mutation. No such source
# exists in Phase 57 -- the vocabulary is declared here so that the day one
# does, the promotion is a data question, not a schema change.
DVF_PREUVES_LOCALES: tuple[str, ...] = ("lot_identifie", "local_identifie")

# Closed vocabulary of what the decision actually VERIFIED. ``parcelle_exacte``
# is the CALLER's fact (a canonical id_parcelle lookup) and is reserved for the
# path that can prove it; the four next ones are proven by the policy itself.
#
# ``type_bien_fourni_utilisateur`` (D-41) is of a DIFFERENT nature and stands
# apart on purpose: it does not describe a mutation, it records that the type of
# the consulted dwelling was DECLARED by the caller. It is what lets a reader of
# the payload tell « the type was declared » from « the type was proven »
# without opening the code -- and it never promotes anything to ``exacte``.
EVIDENCE_TYPE_BIEN_UTILISATEUR = "type_bien_fourni_utilisateur"
DVF_EVIDENCES: tuple[str, ...] = (
    "parcelle_exacte",
    "type_compatible",
    "mono_local",
    "nature_vente",
    "surface_presente",
    EVIDENCE_TYPE_BIEN_UTILISATEUR,
)

# Where the type of the consulted dwelling came from (D-41). ``utilisateur`` is
# a DECLARATION, never a proof of the dwelling's identity: it opens
# ``compatible_parcelle`` and never ``exacte`` (D-37 stays whole). The day an
# identity carries a PROVEN type, this is the value that changes -- not the
# shape of the contract.
DVF_TYPE_BIEN_SOURCES: tuple[str, ...] = ("utilisateur", "inconnu")

# THE closed vocabulary of the CONSULTED dwelling type (D-41). It lives in the
# contract because the contract is what must reject anything outside it; the
# policy (``app.dvf.attribution``) maps each value to a DVF class and
# RE-EXPORTS this tuple as ``TYPES_BIEN_ADMIS`` instead of declaring a second
# list -- two vocabularies drifting apart by one release is exactly the defect
# class this phase closes.
DVF_TYPES_BIEN_CONSULTES: tuple[str, ...] = ("maison", "appartement")

# States that carry a mutation payload, and states that describe no mutation
# at all. An absence holds a reason and nothing else.
_ETATS_AVEC_MUTATION: frozenset[str] = frozenset({"exacte", "compatible_parcelle"})
_ETATS_SANS_PERIMETRE: frozenset[str] = frozenset({"absente", "error"})


class DvfAttribution(BaseModel):
    """Closed decision of the DVF attribution policy (D-10/D-11/D-16/D-37).

    ``exacte`` carries THE sale of the property and REQUIRES a local/lot proof;
    ``compatible_parcelle`` carries a candidate observed on the same parcel --
    « transaction compatible observée sur cette parcelle — non attribuée au
    bien » -- and never a certain price of the dwelling; ``non_attribuable``,
    ``absente`` and ``error`` carry nothing but their reason, and are three
    distinct facts. The validator below is what makes the impossible states
    unrepresentable rather than merely undocumented.

    The parcel's history is NOT here: it stays in ``datasets.dvf`` (D-11/D-17).
    This object qualifies that list, it never replaces it.
    """

    model_config = ConfigDict(extra="forbid")

    status: DvfAttributionStatus
    reason: str | None = Field(
        default=None,
        description=(
            "Raison publique fermée (app.dvf.schemas.DVF_ATTRIBUTION_RAISONS), "
            "obligatoire pour tout état qui ne porte pas de mutation."
        ),
    )
    mutation: ResultatDvf | None = Field(
        default=None,
        description=(
            "Mutation candidate, présente uniquement en `exacte` ou "
            "`compatible_parcelle`. En compatibilité parcellaire, c'est une "
            "transaction observée sur la parcelle, jamais la vente du bien."
        ),
    )
    rule_version: str = Field(
        default=DVF_ATTRIBUTION_RULE_VERSION,
        min_length=1,
        description="Version des règles de décision ayant produit cet état (D-37).",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description=(
            "Preuves effectivement vérifiées, vocabulaire fermé "
            "(app.dvf.schemas.DVF_EVIDENCES). Vide sur une absence, à la seule "
            "exception de `type_bien_fourni_utilisateur`, qui ne décrit aucune "
            "mutation mais la provenance du type consulté (D-41)."
        ),
    )
    type_bien_source: str = Field(
        default="inconnu",
        description=(
            "Provenance du type du bien ayant servi à la décision, vocabulaire "
            "fermé (app.dvf.schemas.DVF_TYPE_BIEN_SOURCES). `utilisateur` "
            "désigne une DÉCLARATION de l'appelant, jamais une preuve "
            "d'identité du logement : elle rend une compatibilité parcellaire "
            "possible, et jamais une attribution exacte (D-41/D-37)."
        ),
    )
    type_bien_consulte: str | None = Field(
        default=None,
        description=(
            "Valeur du type de bien effectivement DÉCLARÉE par l'appelant et "
            "ayant servi à la décision, vocabulaire fermé (app.dvf.schemas."
            "DVF_TYPES_BIEN_CONSULTES). Nulle quand le type est inconnu : "
            "publier la provenance sans sa valeur laisserait le lecteur "
            "deviner SUR QUOI la décision a été prise (D-41)."
        ),
    )
    preuve_locale: str | None = Field(
        default=None,
        description=(
            "Preuve d'identité du local/lot (app.dvf.schemas."
            "DVF_PREUVES_LOCALES). Obligatoire pour `exacte`, interdite "
            "partout ailleurs : la compatibilité parcellaire n'en est pas une."
        ),
    )
    perimetre_ensemble: bool = Field(
        default=False,
        description=(
            "Vrai quand la mutation examinée porte plusieurs locaux (vente "
            "d'ensemble ou vente mixte) : son prix est global (DVF-03)."
        ),
    )
    nb_locaux_residentiel: int | None = Field(
        default=None,
        ge=0,
        description="Nombre de logements dans le périmètre de la mutation examinée.",
    )
    nb_locaux_commercial: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Nombre de locaux commerciaux dans le périmètre de la mutation examinée."
        ),
    )
    nb_lots_source: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Nombre de lots déclaré par la source pour le local attribuable "
            "(métadonnée DGFiP propagée telle quelle, jamais un total calculé)."
        ),
    )

    @model_validator(mode="after")
    def _valide_etat(self) -> "DvfAttribution":
        self._valide_raison()
        self._valide_charge_utile()
        self._valide_preuves()
        self._valide_provenance_type_bien()
        return self

    def _valide_raison(self) -> None:
        raisons_admises = _RAISONS_PAR_ETAT[self.status]
        if self.reason is not None and self.reason not in DVF_ATTRIBUTION_RAISONS:
            raise ValueError(
                f"reason {self.reason!r} n'appartient pas au vocabulaire "
                "d'attribution DVF (app.dvf.schemas.DVF_ATTRIBUTION_RAISONS)"
            )
        if self.reason is not None and self.reason not in raisons_admises:
            raise ValueError(
                f"reason {self.reason!r} n'est pas admissible pour le statut "
                f"{self.status!r}"
            )
        if raisons_admises and self.reason is None:
            raise ValueError(f"le statut {self.status!r} doit porter une raison")

    def _valide_charge_utile(self) -> None:
        if self.status in _ETATS_AVEC_MUTATION and self.mutation is None:
            raise ValueError(
                f"le statut {self.status!r} doit porter sa mutation candidate"
            )
        if self.status not in _ETATS_AVEC_MUTATION and self.mutation is not None:
            raise ValueError(
                f"le statut {self.status!r} ne doit porter aucune mutation : "
                "l'historique de parcelle vit dans datasets.dvf (D-11/D-17)"
            )
        if self.status in _ETATS_SANS_PERIMETRE and (
            self.perimetre_ensemble
            or self.nb_locaux_residentiel is not None
            or self.nb_locaux_commercial is not None
            or self.nb_lots_source is not None
        ):
            raise ValueError(
                f"le statut {self.status!r} ne décrit aucune mutation : il ne "
                "peut porter ni périmètre ni compteur"
            )

    def _valide_preuves(self) -> None:
        if (
            self.preuve_locale is not None
            and self.preuve_locale not in DVF_PREUVES_LOCALES
        ):
            raise ValueError(
                f"preuve_locale {self.preuve_locale!r} n'appartient pas au "
                "vocabulaire ferme (app.dvf.schemas.DVF_PREUVES_LOCALES)"
            )
        if self.status == "exacte" and self.preuve_locale is None:
            raise ValueError(
                "une attribution exacte exige une preuve d'identité locale "
                "explicite (D-37) : la compatibilité parcellaire n'en est pas une"
            )
        if self.status != "exacte" and self.preuve_locale is not None:
            raise ValueError(
                f"le statut {self.status!r} ne doit porter aucune preuve locale"
            )
        inconnues = [item for item in self.evidence if item not in DVF_EVIDENCES]
        if inconnues:
            raise ValueError(
                f"evidence {inconnues!r} hors du vocabulaire ferme "
                "(app.dvf.schemas.DVF_EVIDENCES)"
            )
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("evidence ne doit pas répéter une preuve")
        # ``type_bien_fourni_utilisateur`` est écarté du décompte : il ne
        # constate rien sur une mutation, il trace la provenance de l'ENTRÉE
        # (D-41). « le type a été déclaré et aucune vente n'est enregistrée »
        # reste un fait différent de « le type est inconnu », et une absence
        # doit pouvoir le dire.
        verifiees = [
            item for item in self.evidence if item != EVIDENCE_TYPE_BIEN_UTILISATEUR
        ]
        if self.status in _ETATS_SANS_PERIMETRE and verifiees:
            raise ValueError(
                f"le statut {self.status!r} n'a rien vérifié : evidence doit "
                "rester vide"
            )

    def _valide_provenance_type_bien(self) -> None:
        """Un champ de provenance qui peut mentir n'est pas une provenance.

        Quatre incohérences sont rendues INCONSTRUCTIBLES (D-41) : une source
        ``utilisateur`` sans l'evidence qui l'atteste, l'evidence portée par une
        décision qui se dit prise sur un type inconnu, une source
        ``utilisateur`` sans la VALEUR consultée, et une valeur consultée sur
        une décision qui se dit prise sur un type inconnu. La provenance est
        complète -- source, valeur, trace -- ou elle n'est pas.
        """
        if self.type_bien_source not in DVF_TYPE_BIEN_SOURCES:
            raise ValueError(
                f"type_bien_source {self.type_bien_source!r} n'appartient pas au "
                "vocabulaire fermé (app.dvf.schemas.DVF_TYPE_BIEN_SOURCES)"
            )
        if (
            self.type_bien_consulte is not None
            and self.type_bien_consulte not in DVF_TYPES_BIEN_CONSULTES
        ):
            raise ValueError(
                f"type_bien_consulte {self.type_bien_consulte!r} n'appartient "
                "pas au vocabulaire fermé (app.dvf.schemas."
                "DVF_TYPES_BIEN_CONSULTES)"
            )
        declare = EVIDENCE_TYPE_BIEN_UTILISATEUR in self.evidence
        if self.type_bien_source == "utilisateur" and not declare:
            raise ValueError(
                "type_bien_source 'utilisateur' exige l'evidence "
                f"{EVIDENCE_TYPE_BIEN_UTILISATEUR!r} : une déclaration non "
                "tracée n'est pas une provenance"
            )
        if declare and self.type_bien_source != "utilisateur":
            raise ValueError(
                f"l'evidence {EVIDENCE_TYPE_BIEN_UTILISATEUR!r} exige "
                "type_bien_source 'utilisateur' : la trace et la provenance "
                "voyagent ensemble, ou pas du tout"
            )
        if self.type_bien_source == "utilisateur" and self.type_bien_consulte is None:
            raise ValueError(
                "type_bien_source 'utilisateur' exige la valeur déclarée dans "
                "type_bien_consulte : une provenance sans son objet ne dit pas "
                "sur quoi la décision a été prise"
            )
        if self.type_bien_source == "inconnu" and self.type_bien_consulte is not None:
            raise ValueError(
                f"type_bien_consulte {self.type_bien_consulte!r} exige "
                "type_bien_source 'utilisateur' : une décision prise sur un "
                "type inconnu n'en a consulté aucun"
            )


class RechercheDvfAdresse(BaseModel):
    """What ``/v1/dvf/adresse`` actually searched (C52, B1).

    A proximity search around ONE geocoded point: the first result of the
    instance BAN for the input. The sales listed are those positioned within
    ``rayon_m`` of this point; this is never the history of an address nor
    the identity of a building. ``adresse_non_trouvee``: no address retained,
    no sale read, the empty list is not a zero sale. The input is never
    returned.
    """

    etat: Literal["point_retenu", "adresse_non_trouvee"]
    rayon_m: float
    ban_id: str | None = None
    code_commune: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ReponseDvfAdresse(ReponsePaginee[ResultatDvf]):
    """Paginated envelope of ``/v1/dvf/adresse`` plus what was searched.

    ``recherche`` is absent when nothing was searched (dataset not hosted).
    """

    recherche: RechercheDvfAdresse | None = None


class ProblemeDvfAdresse(ProblemDetail):
    """RFC 9457 problem of ``/v1/dvf/adresse``, never quoting the input."""

    code: Literal["geocodage_indisponible"]
