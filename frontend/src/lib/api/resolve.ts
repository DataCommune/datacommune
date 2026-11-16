import { api } from './client';
import type { OutOfCoverageReason } from '$lib/types/fiche';
import {
	projectDpeAttributionForClient,
	projectEmbeddedDpeInventory,
	type DpeAttributionClient,
	type DpeInventoryPage
} from './dpe-inventory';

export type { DpeAttributionClient, DpeAttributionV2, DpeInventoryPage } from './dpe-inventory';

// --- Types (mirror backend ResultatMultiResolution — resolve/schemas.py L30-103) ---

/**
 * One dataset that was requested but failed at resolve time.
 * NOTE (Pitfall 1): `erreurs` is a LIST of these, never a dict keyed by dataset name.
 */
export interface DatasetErreur {
	dataset: string;
	message: string;
}

/**
 * A non-blocking warning emitted for a dataset (e.g. ADRESSE_INCOMPLETE when the
 * resolved address lacks a street/number → results are commune-level, not bien-level).
 * Always present in the envelope (backend default []).
 */
export interface Avertissement {
	dataset: string;
	code: string;
	message: string;
}

/**
 * Typed presence/absence state for one requested dataset (Phase 44-03, D-04).
 * `absence_reason` uses the Phase 43 taxonomy plus `source_delayed`.
 */
export interface ProxySourceState {
    statut: 'disponible' | 'vide' | 'partiel' | 'indisponible';
    nombre_retournes: number | null;
    total: number | null;
    complet: boolean | null;
    limite: number | null;
    pages_lues: number;
}

export interface DatasetResolutionState {
    sources?: Record<string, ProxySourceState> | null;
	dataset: string;
	presence: 'value' | 'absent' | 'error';
	absence_reason: string | null;
	raison: string | null;
	/** Present only with `absence_reason: 'out_of_coverage'` (D-24). */
	motif?: OutOfCoverageReason;
}

/**
 * Machine-readable resolution outcome (Phase 45, D-04) carried in `meta.resolution`:
 * `exact`/`ambiguous` qualify a non-empty result; `not_reported`/`out_of_coverage`
 * type an empty one. `parcelles_candidates` lists the stable-ordered cadastre
 * candidates when the issue is `ambiguous`.
 */
export interface ResolutionOutcome {
	issue: 'exact' | 'ambiguous' | 'not_reported' | 'out_of_coverage';
	raison: string | null;
	parcelles_candidates: string[] | null;
	/** Present only with `issue: 'out_of_coverage'` (D-24). */
	motif?: OutOfCoverageReason;
}

// --- Phase 51 : contrat d'attribution DPE (miroir de app/dpe/schemas.py) --------------------
//
// D-18 : le frontend ne rejoue AUCUNE règle d'attribution. Il lit une décision fermée. Les
// types ci-dessous sont donc écrits pour rendre inconstructible ce que le `model_validator`
// Pydantic rejette : un état `absent` qui porterait un diagnostic, un `building` qui porterait
// une carte, un `exact` sans diagnostic. Une variante trop permissive ici recréerait, côté
// TypeScript, la latitude d'interprétation que la Phase 51 retire côté API.
//
// Ce qui est structurellement ABSENT de toutes ces variantes (T-51-16) : la clé BAN de
// jointure, le lien de remplacement ADEME et le complément d'adresse brut. Les deux premiers
// sont des données internes ; le troisième n'est jamais reparsé en TypeScript — sa lecture
// structurée (`etage`/`batiment`/`porte`) est produite par le backend.

/**
 * Les CINQ états fermés de la politique d'attribution (D-13). Ils ne sont pas quatre états plus
 * un cas d'erreur : `absent` (aucun DPE rattaché exactement) et `error` (le sous-système DPE a
 * échoué) sont deux faits distincts, et `building` (preuve à l'échelle du bâtiment seulement)
 * n'est ni une absence ni une attribution.
 */
export type DpeAttributionStatus = 'exact' | 'multiple' | 'building' | 'absent' | 'error';

/**
 * Vocabulaire fermé des raisons publiques (clés de `DPE_ATTRIBUTION_RAISONS`). Jamais une
 * exception amont, jamais une requête, jamais un mot de vérité inventé côté client.
 */
export type DpeAttributionRaison =
	| 'identity_unavailable'
	| 'not_reported'
	| 'ambiguous_unresolved'
	| 'truncated'
	| 'temporary_error';

/**
 * Un diagnostic exact ET distinguable proposé au choix de l'utilisateur (D-07). Minimisé par
 * l'allowlist backend : exactement ce qu'une carte rend, rien de plus.
 */
export interface DpeCandidate {
	numero_dpe: string;
	etage: number | null;
	batiment: string | null;
	porte: string | null;
	surface_habitable: number | null;
	etiquette_dpe: string | null;
	annee_diagnostic: number | null;
	perime: boolean;
}

/**
 * LE diagnostic attribuable au bien (D-01/D-10) — surensemble de `DpeCandidate` restreint à ce
 * que le KPI et le thème Énergie rendent réellement, plus les dates de validité lues à la
 * source. C'est la seule forme qu'un KPI ou une ligne Énergie a le droit de lire.
 */
export interface DpePublicDiagnostic {
	numero_dpe: string;
	etage: number | null;
	batiment: string | null;
	porte: string | null;
	type_batiment: string | null;
	surface_habitable: number | null;
	etiquette_dpe: string | null;
	etiquette_ges: string | null;
	annee_construction: number | null;
	periode_construction: string | null;
	type_installation_chauffage: string | null;
	type_energie_principale_chauffage: string | null;
	conso_5_usages_ep: number | null;
	emission_ges_5_usages: number | null;
	cout_total_5_usages: number | null;
	qualite_isolation_enveloppe: string | null;
	date_etablissement_dpe: string | null;
	date_fin_validite_dpe: string | null;
	annee_diagnostic: number | null;
	perime: boolean;
}

/**
 * Agrégat à la granularité BÂTIMENT — jamais un diagnostic individuel (D-12). Un effectif et
 * une répartition, rien qui nomme un logement. `distribution` est calculée par le backend et
 * n'élit AUCUNE classe principale : lire « la meilleure lettre du bâtiment » comme la lettre du
 * bien est précisément la substitution que cette phase retire.
 */
export interface DpeBuildingContext {
	count: number;
	distribution: Record<string, number>;
	truncated: boolean;
}

/**
 * `exact` : la preuve est là. Le diagnostic est OBLIGATOIRE, et c'est le seul état qui en porte
 * un. Ni raison, ni candidat, ni agrégat — sa charge utile EST la réponse.
 */
export interface DpeAttributionExacte {
	status: 'exact';
	reason?: null;
	diagnostic: DpePublicDiagnostic;
	/** Tuple vide : seul `multiple` peut porter des candidats (le backend sérialise `[]` ici). */
	candidates?: [];
	building_context?: null;
}

/**
 * `multiple` : plusieurs diagnostics exacts et distinguables, aucun attribuable sans choix de
 * l'utilisateur (D-07). Le backend en garantit au moins deux ; aucun n'alimente la Fiche tant
 * que `?dpe=` n'a pas été validée PAR APPARTENANCE à cette liste (D-09).
 */
export interface DpeAttributionMultiple {
	status: 'multiple';
	reason?: null;
	diagnostic?: null;
	candidates: DpeCandidate[];
	building_context?: null;
}

/** `building` : preuve à l'échelle du bâtiment seulement — agrégat obligatoire, zéro carte. */
export interface DpeAttributionBatiment {
	status: 'building';
	reason: 'not_reported' | 'ambiguous_unresolved' | 'truncated';
	diagnostic?: null;
	candidates?: [];
	building_context: DpeBuildingContext;
}

/**
 * `absent` : aucun DPE rattaché exactement à cette adresse (D-11), ou aucune identité BAN
 * résolue (D-04). C'est un état de PREMIER RANG, pas un cas d'erreur : depuis le Plan 51-03 il
 * couvre aussi les clés BAN orphelines, que plus aucun repli géométrique ne rattrape.
 */
export interface DpeAttributionAbsente {
	status: 'absent';
	reason: 'identity_unavailable' | 'not_reported';
	diagnostic?: null;
	candidates?: [];
	building_context?: null;
}

/** `error` : le sous-système DPE a échoué. Jamais reformulé en absence (D-13/D-14). */
export interface DpeAttributionErreur {
	status: 'error';
	reason: 'temporary_error';
	diagnostic?: null;
	candidates?: [];
	building_context?: null;
}

/** Décision fermée de la politique d'attribution — l'unique vérité DPE de la réponse. */
export type DpeAttribution =
	| DpeAttributionExacte
	| DpeAttributionMultiple
	| DpeAttributionBatiment
	| DpeAttributionAbsente
	| DpeAttributionErreur
	| DpeAttributionClient;

// --- Phase 57 (D-16/D-37) : la décision DVF, miroir de `app/dvf/schemas.py` -------------------

/**
 * Les CINQ états fermés de la politique d'attribution DVF (D-16). Même doctrine que le DPE :
 * `non_attribuable` (une mutation existe mais ne peut pas être rattachée à ce bien), `absente`
 * (rien trouvé, ou rien même cherché) et `error` (panne technique) sont TROIS faits distincts.
 * `exacte` exige une preuve d'identité locale ; aucune source de preuve n'existant à ce jour,
 * le backend ne le produit jamais (D-37) — l'état est déclaré pour le jour où elle existera.
 */
export type DvfAttributionStatus =
	'exacte' | 'compatible_parcelle' | 'non_attribuable' | 'absente' | 'error';

/** Raisons admises par `non_attribuable` (clés de `DVF_ATTRIBUTION_RAISONS`). */
export type DvfRaisonNonAttribuable =
	| 'derniere_vente_mixte'
	| 'derniere_vente_commerciale'
	| 'vente_ensemble'
	| 'type_incompatible'
	| 'classe_non_residentielle'
	| 'vefa_sans_surface'
	| 'vente_sur_plan'
	| 'nature_hors_vente'
	| 'type_bien_inconnu';

/** Raisons admises par `absente` puis par `error` — jamais interchangeables (D-16). */
export type DvfRaisonAbsente = 'aucune_mutation' | 'parcelle_indisponible';
export type DvfRaisonErreur = 'entree_illisible' | 'temporary_error';

/** Vocabulaire fermé des raisons publiques DVF, tous états confondus. */
export type DvfAttributionRaison = DvfRaisonNonAttribuable | DvfRaisonAbsente | DvfRaisonErreur;

/** Preuves d'identité du local/lot — les SEULES qui promeuvent un candidat en `exacte` (D-37). */
export type DvfPreuveLocale = 'lot_identifie' | 'local_identifie';

/**
 * Le type du bien consulté tel que le lecteur peut le DÉCLARER, ou son absence (D-41). `null`
 * est « Non précisé » : ce n'est pas une troisième valeur d'API, c'est l'absence du paramètre.
 */
export type TypeBienDeclare = 'maison' | 'appartement' | null;

/**
 * Ce que la politique a effectivement vérifié sur le candidat retenu (vocabulaire fermé).
 *
 * `type_bien_fourni_utilisateur` (D-41) est d'une autre nature et l'assume : il ne constate rien
 * sur une mutation, il trace que le type du bien consulté a été DÉCLARÉ par l'appelant. C'est ce
 * qui permet de distinguer « le type a été déclaré » de « le type a été prouvé » sans ouvrir le
 * code — et il ne promeut jamais rien en `exacte`.
 */
export type DvfEvidence =
	| 'parcelle_exacte'
	| 'type_compatible'
	| 'mono_local'
	| 'nature_vente'
	| 'surface_presente'
	| 'type_bien_fourni_utilisateur';

/**
 * Mutation candidate portée par la décision — miroir de `ResultatDvf` restreint à ce que la
 * Fiche rend réellement.
 *
 * `valeur_fonciere` et `prix_m2` admettent une CHAÎNE en plus du nombre, et ce n'est pas un
 * relâchement du contrat backend : la minimisation D-03 du loader (`+page.ts`) remplace ces deux
 * montants par une tranche de prix (« 200 000 – 250 000 € ») AVANT sérialisation quand la
 * mutation est isolée et récente. Le type décrit donc ce que le frontend reçoit réellement après
 * `load()`, pas seulement ce que le backend sérialise.
 */
export interface DvfMutation {
	id_mutation: string;
	date_mutation: string;
	nature_mutation: string | null;
	valeur_fonciere: number | string | null;
	type_local: string | null;
	surface_reelle_bati: number | null;
	nombre_pieces_principales: number | null;
	prix_m2: number | string | null;
	id_parcelle: string | null;
	classe_mutation: string | null;
	prix_m2_raison: string | null;
	mutation_mono_local: boolean | null;
	surface_bati_totale: number | null;
	hors_stats_vefa: boolean | null;
}

/**
 * Métadonnées communes aux cinq états : version de règle, preuves et périmètre de la mutation
 * examinée. Toutes optionnelles côté client — une fixture antérieure à la Phase 57 reste un
 * littéral TypeScript valide, exactement comme pour les champs additifs des Phases 44/45.
 */
interface DvfAttributionBase {
	/** Version des règles de décision ayant produit cet état (D-37). */
	rule_version?: string;
	evidence?: DvfEvidence[];
	/** Vrai quand la mutation examinée porte plusieurs locaux : son prix est GLOBAL (DVF-03). */
	perimetre_ensemble?: boolean;
	nb_locaux_residentiel?: number | null;
	nb_locaux_commercial?: number | null;
	nb_lots_source?: number | null;
	/**
	 * Provenance du type ayant servi à la décision (D-41). `'utilisateur'` désigne une
	 * DÉCLARATION de l'appelant, jamais une preuve d'identité du logement : elle rend une
	 * compatibilité parcellaire possible, et jamais une attribution exacte.
	 */
	type_bien_source?: 'utilisateur' | 'inconnu';
	/**
	 * La VALEUR du type effectivement déclarée et ayant servi à la décision (D-41). `null`
	 * quand le type est inconnu : le backend rend inconstructible le couple
	 * `type_bien_source: 'utilisateur'` sans valeur, et l'inverse. Publier la source sans sa
	 * valeur laissait le lecteur deviner SUR QUOI la décision avait été prise.
	 */
	type_bien_consulte?: 'maison' | 'appartement' | null;
}

/** `exacte` : LA vente du bien. Exige une preuve d'identité locale, jamais produite en 57. */
export interface DvfAttributionExacte extends DvfAttributionBase {
	status: 'exacte';
	reason?: null;
	mutation: DvfMutation;
	preuve_locale: DvfPreuveLocale;
}

/**
 * `compatible_parcelle` : une transaction compatible OBSERVÉE SUR CETTE PARCELLE. Ce n'est ni
 * « la vente de ce bien », ni un prix certain du logement (D-37) : sans preuve d'identité
 * locale, la compatibilité parcellaire reste une compatibilité.
 */
export interface DvfAttributionCompatible extends DvfAttributionBase {
	status: 'compatible_parcelle';
	reason?: null;
	mutation: DvfMutation;
	preuve_locale?: null;
}

/** `non_attribuable` : une mutation existe, elle ne peut pas être rattachée à ce bien. */
export interface DvfAttributionNonAttribuable extends DvfAttributionBase {
	status: 'non_attribuable';
	reason: DvfRaisonNonAttribuable;
	mutation?: null;
	preuve_locale?: null;
}

/** `absente` : aucune mutation observée, ou aucune parcelle résolue à interroger. */
export interface DvfAttributionAbsente extends DvfAttributionBase {
	status: 'absente';
	reason: DvfRaisonAbsente;
	mutation?: null;
	preuve_locale?: null;
}

/** `error` : le sous-système DVF a échoué. Jamais reformulé en « aucune vente ici » (D-16). */
export interface DvfAttributionErreur extends DvfAttributionBase {
	status: 'error';
	reason: DvfRaisonErreur;
	mutation?: null;
	preuve_locale?: null;
}

/** Décision fermée de la politique d'attribution — l'unique vérité DVF de la réponse. */
export type DvfAttribution =
	| DvfAttributionExacte
	| DvfAttributionCompatible
	| DvfAttributionNonAttribuable
	| DvfAttributionAbsente
	| DvfAttributionErreur;

/**
 * Multi-dataset resolution envelope element returned by GET /v1/resolve.
 *
 * Three independent sources of per-dataset state (read together in bien-fiche.ts):
 *   - presence of the key in `datasets` (absent = never requested)
 *   - `datasets[name]` value: null / empty list = "empty", real payload = "ok"
 *   - presence in the `erreurs` LIST = "error"
 *
 * `commune` is the commune NAME, NOT the INSEE code (Pitfall 4) — the canonical
 * INSEE code is exposed as `code_insee` (Phase 45). `datasets` values are
 * HETEROGENEOUS (object | list | null depending on the dataset — Pitfall 2),
 * hence `unknown`.
 */
export interface ResultatMultiResolution {
	type_detecte: string; // "gps" for a coordinate resolution
	latitude: number;
	longitude: number;
	adresse: string | null;
	id_parcelle: string | null; // canonical parcel (null when absent/ambiguous)
	commune: string | null; // NAME, not the INSEE code (Pitfall 4)
	code_postal: string | null;
	score: number | null;
	// Phase 44-03/45 additive fields — optional so pre-existing fixtures and
	// snapshots (built before these fields) remain valid TS literals.
	code_insee?: string | null; // canonical INSEE code, same for all 3 input modes
	/**
	 * BAN interoperability key present in historical address/GPS responses. It is a DPE target
	 * only when the address mode established the complete source address; a GPS reverse-geocode
	 * remains display context and must not be forwarded to the document inventory.
	 */
	ban_id?: string | null;
	resolution?: 'exact' | 'ambiguous'; // parcel-resolution issue for this result
	parcelles_candidates?: string[] | null; // only populated when ambiguous
	/**
	 * Phase 51 (D-15/D-18) — LA décision DPE de la réponse, additive et optionnelle côté resolve
	 * (`DpeAttribution | None` backend). Depuis le Plan 51-02, `datasets.dpe` ne porte plus qu'un
	 * diagnostic en état `exact` et reste vide dans les quatre autres : toute distinction visible
	 * (ambiguïté, granularité bâtiment, absence, panne) se lit ICI, jamais dans le bloc dataset ni
	 * dans `dataset_states.dpe`, qui reste volontairement grossier (`multiple` et `building` y
	 * valent tous deux `value`).
	 */
	dpe_attribution?: DpeAttribution | null;
	/** Versioned, field-projected inventory; independent from attribution and KPI selection. */
	dpe_inventory?: DpeInventoryPage | null;
	/**
	 * Phase 57 (D-16/D-37) — LA décision DVF de la réponse, additive et optionnelle. L'état de
	 * liaison se lit ICI, sur `status`, JAMAIS sur le remplissage de `datasets.dvf` : ce bloc
	 * continue de porter l'historique de la parcelle (D-11/D-17), que cette décision qualifie
	 * sans jamais le remplacer. Absente de l'enveloppe = décision non calculée, ce qui n'est pas
	 * la preuve d'une absence de vente (même doctrine que `dpe_attribution`).
	 */
	dvf_attribution?: DvfAttribution | null;
	datasets: Record<string, unknown> | null; // heterogeneous shapes (Pitfall 2)
	erreurs: DatasetErreur[] | null; // a LIST, not a dict (Pitfall 1)
	avertissements: Avertissement[]; // always present (backend default [])
	dataset_states?: Record<string, DatasetResolutionState> | null; // Phase 44-03
}

/**
 * Public provenance contract for one dataset (Phase 44, D-02) — EXACT mirror of
 * `api/backend/app/common/schemas.py::DatasetProvenance` (+ `SourcePeriod`, `DatasetCoverage`,
 * `PublicationCadence`, `EditorialState`), read-only: the backend is never amended from here.
 *
 * Phase 49 (Pitfall 1): `/v1/resolve` has been putting this list on the wire since Phase 44 —
 * `MetaResolve.source` was typed `unknown` and dropped by the loader. The millésime and the
 * source shown in the ℹ️ popover come from HERE (live), never from a static frontend constant
 * that would turn false at the first ETL refresh (D-13).
 *
 * `periode.statut = 'inconnue'` is an honest unknown, NOT a placeholder to fill client-side:
 * the backend explicitly refuses to fabricate a period from an internal import timestamp.
 */
export interface DatasetProvenance {
	nom: string;
	licence: string;
	url: string;
	producteur: string;
	dataset: string;
	periode: {
		statut: 'connue' | 'glissante' | 'inconnue';
		label: string | null;
		debut: string | null;
		fin: string | null;
	};
	/** ISO-8601 datetime of the last successful import, or null when never imported. */
	derniere_importation_reussie: string | null;
	couverture: {
		type: 'departements' | 'nationale' | 'amont' | 'inconnue';
		departements: string[] | null;
	};
	cadence: {
		statut: 'official' | 'non_publiee_ou_irreguliere';
		label: string | null;
		evidence_url: string | null;
		intervalle_jours: number | null;
	};
	etat: 'actuel' | 'retarde' | 'erreur' | 'inconnu';
}

/** Typed metadata for GET /v1/resolve (source attribution + resolution outcome). */
export interface MetaResolve {
	source?: DatasetProvenance[];
	resolution?: ResolutionOutcome | null;
}

/**
 * Envelope for GET /v1/resolve.
 * `donnees` is a LIST with a single element for a GPS resolution → read `donnees[0]`.
 * An empty `donnees` carries its typed absence in `meta.resolution`.
 */
export interface ReponseResolve {
	statut: string;
	donnees: ResultatMultiResolution[];
	meta?: MetaResolve;
}

/**
 * Datasets requested for the FAST leg of a Fiche Bien. `commune` is INCLUDED on purpose
 * (Pitfall 4): the resolver returns the full CommuneSynthese in the envelope, so a single
 * round-trip brings back both the bien-level datasets and the commune block (no 2nd
 * /v1/commune call).
 *
 * `risques` and `urbanisme` are DELIBERATELY absent from this list (Phase 50): both are LIVE
 * PROXY datasets (Géorisques, GPU/IGN) whose upstream call is bounded only by the backend's
 * 30 s httpx timeout, and the whole request was measured at up to 11,6 s on a cold point in
 * production while every dataset kept here answers in < 0,3 s. Requesting them together made
 * the SSR render of the entire fiche wait for the slowest upstream. They moved to
 * `DATASETS_BIEN_PROXIES` below, fetched by a second call the loader streams.
 */
const DATASETS_BIEN = 'cadastre,dpe,dvf,commune';

/**
 * SLOW leg of a Fiche Bien — the two live-proxy datasets split out of `DATASETS_BIEN` above.
 * Kept as one call (not two): the backend resolves the requested datasets concurrently, so the
 * pair costs the slower of the two, and a single round-trip keeps one merge point on the page.
 */
const DATASETS_BIEN_PROXIES = 'risques,urbanisme';

// --- API function ---

/**
 * Resolve a property by GPS coordinates.
 * The query is built via URLSearchParams (automatic encoding) — never concatenate raw
 * lat/lon into an unescaped string. Reuses the `api()` wrapper (X-API-Key + ApiError);
 * never raw-fetch the DataCommune API.
 *
 * `fetchImpl` mirrors `getCommuneSynthese` (commune.ts) — SSR-safe: a universal `load()`
 * on the Cloudflare Worker must pass SvelteKit's instrumented fetch, or the request
 * silently fails/times out (RESEARCH Pitfall 2, D-04).
 *
 * Returns `null` on an empty envelope: a 200 `{ donnees: [] }` is the documented backend
 * response for coordinates outside the loaded perimeter. Surfacing `null` (not `undefined`)
 * lets the caller route to the dedicated empty-state screen instead of dereferencing an
 * undefined element and falling into the generic error path (CR-01).
 */
export async function resolveBien(
	lat: number,
	lon: number,
	fetchImpl?: typeof fetch
): Promise<ResultatMultiResolution | null> {
	const qs = new URLSearchParams({
		q: `${lat},${lon}`,
		type: 'gps',
		datasets: DATASETS_BIEN
	});
	const res = await api<ReponseResolve>(`/v1/resolve?${qs}`, { cache: 'no-store' }, fetchImpl);
	const multi = res.donnees[0] ?? null;
	return multi == null ? null : projectDpeContracts(multi);
}

/**
 * The USEFUL part of the second /v1/resolve call (Phase 50) — everything the Fiche Bien has to
 * merge back into the fast leg's result, and nothing else. The identity fields
 * (adresse/id_parcelle/commune/score/resolution) are NOT carried: they are already resolved by
 * the fast leg, and a second copy could only introduce a divergence between two renders of the
 * same address.
 *
 * `provenance` is `meta.source` of that second call — the millésime/source lines of the ℹ️
 * popovers and of the printed sources table for `risques`/`urbanisme` come from HERE, live
 * (D-13), exactly like the fast leg's own `meta.source`.
 */
export interface EnveloppeProxiesBien {
	datasets: Record<string, unknown> | null; // heterogeneous shapes (Pitfall 2)
	erreurs: DatasetErreur[] | null; // a LIST, not a dict (Pitfall 1)
	avertissements: Avertissement[];
	dataset_states: Record<string, DatasetResolutionState> | null;
	provenance: DatasetProvenance[] | null;
}

/**
 * Resolve the two LIVE PROXY datasets of a Fiche Bien (`risques`, `urbanisme`) by GPS
 * coordinates — the slow leg of the Phase 50 split (see `DATASETS_BIEN`). Called with the
 * coordinates the fast leg already resolved, so this second call never re-geocodes an address.
 *
 * SSR-safe like `resolveBien` (`fetchImpl` transmitted, RESEARCH Pitfall 2 / D-04), same
 * URLSearchParams encoding and same `api()` wrapper — never a raw fetch.
 *
 * Returns `null` on an empty envelope (200 `{ donnees: [] }`): nothing to merge, and the caller
 * then renders the honest degradation of the two themes instead of dereferencing an undefined
 * element (same contract as `resolveBien`, CR-01).
 */
export async function resolveBienProxies(
	lat: number,
	lon: number,
	fetchImpl?: typeof fetch
): Promise<EnveloppeProxiesBien | null> {
	const qs = new URLSearchParams({
		q: `${lat},${lon}`,
		type: 'gps',
		datasets: DATASETS_BIEN_PROXIES
	});
	const res = await api<ReponseResolve>(`/v1/resolve?${qs}`, {}, fetchImpl);
	const multi = res.donnees[0] ?? null;
	if (multi == null) return null;
	return {
		datasets: multi.datasets,
		erreurs: multi.erreurs,
		avertissements: multi.avertissements ?? [],
		dataset_states: multi.dataset_states ?? null,
		provenance: res.meta?.source ?? null
	};
}

/**
 * Resolve a property by free-text address (D-04 fallback: `/v1/resolve` exposes no
 * stable BAN id — the caller carries the address as a slugified URL token instead,
 * see `utils/slug.ts`). SSR-safe like `resolveBien` (fetchImpl transmitted).
 *
 * Returns the FULL envelope (not `donnees[0]`): the loader needs `meta.resolution`
 * (exact/ambiguous/not_reported/out_of_coverage) to render the precision badge
 * (Plan 02/05), not just the resolved property.
 */
export async function resolveBienParAdresse(
	texte: string,
	fetchImpl?: typeof fetch,
	typeBien?: string | null
): Promise<ReponseResolve> {
	const qs = new URLSearchParams({
		q: texte,
		type: 'adresse',
		datasets: DATASETS_BIEN
	});
	// D-41 — le type du bien consulté est DÉCLARÉ, et le paramètre n'existe que pour les deux
	// valeurs du vocabulaire fermé du backend. « Non précisé » n'envoie RIEN : c'est l'ABSENCE du
	// paramètre, jamais une troisième valeur d'API. Une valeur exotique n'est jamais relayée non
	// plus — le backend la rejetterait en 422 (D-40), et une faute de frappe dans l'URL ferait
	// alors tomber une page publique.
	if (typeBien === 'maison' || typeBien === 'appartement') {
		qs.set('type_bien', typeBien);
	}
	const response = await api<ReponseResolve>(`/v1/resolve?${qs}`, { cache: 'no-store' }, fetchImpl);
	for (const multi of response.donnees) projectDpeContracts(multi);
	return response;
}

/**
 * Sanitize only the new DPE surfaces and keep the historic multi-dataset envelope intact.
 * This preserves DVF/cadastre/commune data even when the attribution contract is obsolete.
 */
function projectDpeContracts(multi: ResultatMultiResolution): ResultatMultiResolution {
	if (multi.datasets !== null && Object.hasOwn(multi.datasets, 'dpe')) {
		// V2 carries documents only through dpe_inventory. A legacy exact dataset must not remain
		// available to another mapper after the attribution itself has failed closed.
		multi.datasets.dpe = null;
	}
	if (multi.dpe_inventory !== undefined && multi.dpe_inventory !== null) {
		multi.dpe_inventory = projectEmbeddedDpeInventory(multi.dpe_inventory);
	}
	if (multi.dpe_attribution !== undefined && multi.dpe_attribution !== null) {
		multi.dpe_attribution = projectDpeAttributionForClient(multi.dpe_attribution);
	}
	return multi;
}
