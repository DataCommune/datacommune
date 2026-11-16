// Address search and address identity of this instance's API (04-02, D-105..D-107, LIEU-01).
// Types mirror `api/backend/app/adresse/schemas.py` (`contracts/openapi.v1.json`). Nothing here
// knows which departements the instance hosts, nor which order the result types come in: the
// order (`ordre_types`), the coverage (`hebergement.ban`, `couverture`) and the parcel relation
// (`identite.parcelle`) are rendered as served, never recomputed (D-106).
import { api } from './client';
import type { DatasetHosting } from './admin';
import type { OutOfCoverageReason } from '$lib/types/fiche';
import { detailPanne, type DetailPanne } from '$lib/data/panne';
import { projeterRapport, type RapportPublic } from './rapport';

// --- Search (`GET /v1/adresses/recherche`) ---------------------------------------------------

export type TypeResultat = 'adresse' | 'commune';

export interface DepartementRef {
	code: string;
	nom: string | null;
}

/**
 * One search result: a numbered BAN address, or a street without number (`voie`, D-107). A street
 * has neither `numero` nor `ban_id`: an address page needs a number.
 */
export interface ResultatRechercheAdresse {
	type_resultat: 'adresse' | 'voie';
	ban_id: string | null;
	numero: string | null;
	rep: string | null;
	nom_voie: string | null;
	code_postal: string | null;
	code_commune: string | null;
	nom_commune: string | null;
	departement: DepartementRef | null;
}

/** Territory named by the input; a postal code names its departement only (name left null). */
export interface TerritoireRecherche {
	code_insee: string | null;
	nom: string | null;
	departement: DepartementRef;
}

/** BAN hosting of a search: the territory typed when it is out of coverage, or its homonyms. */
export interface HebergementBanRecherche extends DatasetHosting {
	territoire?: TerritoireRecherche | null;
	homonymes?: TerritoireRecherche[];
}

/** Number/suffix match qualified by the API within the returned results only. */
export interface CorrespondanceNumeroRecherche {
	etat: 'exacte' | 'proches' | 'aucune_dans_resultats';
	numero_demande: number;
	suffixe_demande: string | null;
	portee: 'resultats_retournes';
}

export interface RechercheAdresses {
	ordre_types: TypeResultat[];
	resultats: ResultatRechercheAdresse[];
	hebergement: { ban: HebergementBanRecherche };
	/** Optional while older API instances remain compatible with this client. */
	correspondance_numero?: CorrespondanceNumeroRecherche | null;
	/**
	 * `trop_large` (R-08): the national approximate read ran out of its budget; `resultats` is empty
	 * and proves no absence. Neither an outage (that is a 503) nor a zero. Absent from older APIs.
	 */
	etat?: 'aboutie' | 'trop_large';
}

interface ReponseApi<T> {
	statut: string;
	donnees: T;
	meta?: { source?: unknown } | null;
}

export interface SearchAdressesOptions {
	departement?: string;
	limite?: number;
	signal?: AbortSignal;
}

/**
 * The only address search of the site (D-105): this instance's BAN, never a third-party service.
 * `q` is sent as typed. A failure is thrown (`ApiError`, typed by `detailPanne`), never returned as
 * an empty list: a 503 `geocodage_indisponible` is an outage, not a zero (SEM-01).
 */
export async function searchAdresses(
	q: string,
	options: SearchAdressesOptions = {},
	fetchImpl: typeof fetch = fetch
): Promise<RechercheAdresses> {
	const sp = new URLSearchParams({ q });
	if (options.departement) sp.set('departement', options.departement);
	if (options.limite !== undefined) sp.set('limite', String(options.limite));
	const res = await api<ReponseApi<RechercheAdresses>>(
		`/v1/adresses/recherche?${sp.toString()}`,
		options.signal ? { signal: options.signal } : {},
		fetchImpl
	);
	return res.donnees;
}

// --- Identity (`GET /v1/adresse/{ban_id}`) ---------------------------------------------------

export type EtatParcelle =
	| 'unique'
	| 'ambigue'
	| 'point_hors_parcelle'
	| 'hors_couverture'
	| 'indisponible'
	| 'indeterminee';

/**
 * Public model of an address page: the fields the page renders, picked one by one from the served
 * identity. The BAN point is not served by the API, and nothing else (a coordinate, a header) can
 * reach the serialized SSR model through this projection.
 */
export interface FicheAdressePublique {
	identite: {
		etat: 'etablie' | 'indeterminee';
		ban_id: string;
		libelle: string | null;
		numero: string | null;
		rep: string | null;
		nom_voie: string | null;
		code_postal: string | null;
		commune: { code_insee: string; nom: string | null };
		departement: DepartementRef;
		/**
		 * `id_parcelle` is served for `unique` only: the parcel that contains the BAN point. `motif` is
		 * kept for `indeterminee` only (`positions_contradictoires`, `position_absente`): it tells the
		 * page whether the sources disagree on the position (2026-10-05, 04-DECISIONS-TEXTES row 7).
		 */
		parcelle: { etat: EtatParcelle; id_parcelle: string | null; nombre_candidats?: number | null; motif?: string | null } | null;
	};
	couverture: { etat: 'heberge' | 'hors_couverture'; motif: OutOfCoverageReason | null };
	/** The four landmarks of the place (04-03, D-113); null when nothing of the place is read. */
	reperes?: ReperesAdresse | null;
	/** The themes of the place in the D-115 order (04-06); empty when nothing of the place is read. */
	themes?: ThemeLieu[];
}

// --- Themes of the place (`themes` of `GET /v1/adresse/{ban_id}`, 04-06, D-104, D-115) ---------

/** One item of a served list: label as published, distance to the point when measured. */
export interface ElementLieu {
	libelle: string;
	distance_m: number | null;
	nature: string | null;
}

/** One served value of a theme: a `ValeurLieu` that may be a boolean or a list. */
export interface ValeurTheme extends Omit<ValeurLieu, 'valeur'> {
	valeur: boolean | number | string | null;
	elements: ElementLieu[];
	liste_incomplete: boolean;
	rayon_m: number | null;
}

export interface GroupeTheme {
	cle: string;
	titre: string | null;
	champs: ValeurTheme[];
}

export type CleThemeLieu = 'batiment_parcelle' | 'risques' | 'urbanisme' | 'reseaux_transports';

/** One theme of the place as served: a state without value keeps its reason and no group. */
export interface ThemeLieu {
	cle: CleThemeLieu;
	titre: string;
	applicable: boolean;
	etat: 'value' | 'not_available' | 'out_of_coverage' | 'temporary_error';
	motif: string | null;
	groupes: GroupeTheme[];
	liste_incomplete: boolean;
}

// --- Landmarks of the place (`reperes` of `GET /v1/adresse/{ban_id}`, 04-03) --------------------

/** Served state of a value of the place (`ValeurLieu.etat`, SEM-01). */
export type EtatValeurLieu =
	| 'value'
	| 'not_reported'
	| 'out_of_coverage'
	| 'temporary_error'
	| 'not_applicable'
	| 'insufficient_sample'
	| 'not_available'
	| 'zero_denominator'
	| 'suppressed'
	| 'not_qualified';

/** How a value relates to the address (closed list of `fiche-fields`, SEM-02). */
export type Rattachement =
	| 'adresse_saisie_diagnostiqueur'
	| 'parcelle_du_point'
	| 'batiment_du_point'
	| 'batiment_rnb_de_l_adresse'
	| 'au_point'
	| 'rayon_du_point'
	| 'commune_de_l_adresse'
	| 'eligibilite_adresse'
	| 'commune_reference'
	| 'adresse_declaree_vente';

/** Source of a served value: catalogue key, name, producer, https URL. */
export interface SourceValeur {
	cle: string;
	nom: string;
	producteur: string | null;
	url: string | null;
}

/** Reference value of the commune (D-103): never a datum of the place. */
export interface ReferenceCommune {
	valeur: number | null;
	unite: string;
	territoire: { code_insee: string; nom: string | null };
	source: SourceValeur | null;
	millesime: string | null;
	url: string | null;
}

/** One served value of the place with its state and its meaning (`ValeurLieu`). */
export interface ValeurLieu {
	field_id: string;
	etat: EtatValeurLieu;
	valeur: number | string | null;
	unite: string | null;
	periode: string | null;
	motif: string | null;
	source: SourceValeur | null;
	rattachement: Rattachement | null;
	reference: ReferenceCommune | null;
}

/** A document named by the most-recent selection: number and classes. */
export interface DocumentSelectionne {
	numero: string;
	etiquette_dpe: string | null;
	etiquette_ges: string | null;
	date_etablissement: string | null;
}

/** Closed list of the served reasons of the selection (04-03). */
export type MotifSelection =
	| 'document_valide'
	| 'ancienne_methode'
	| 'expire'
	| 'remplace'
	| 'remplacement_incertain'
	| 'validite_inconnue'
	| 'aucun_document_valide'
	| 'aucun_document_date'
	| 'aucun_document'
	| 'population_incomplete';

/** Most recent document of the whole population, never of a page (D-109, D-111). */
export interface SelectionPlusRecent {
	etat: 'unique' | 'egalite' | 'indeterminee';
	motif: MotifSelection | string;
	date: string | null;
	document: DocumentSelectionne | null;
	documents_ex_aequo: DocumentSelectionne[];
}

export interface RepereDpe extends ValeurLieu {
	selection: SelectionPlusRecent | null;
}

/** The landmarks in their display order (D-113): ① DPE, ② sales, ③ clay, ④ commune reference. */
export interface ReperesAdresse {
	dpe: RepereDpe;
	ventes: { nombre: ValeurLieu; derniere: ValeurLieu };
	argiles: ValeurLieu;
	reference_commune: { maisons: ValeurLieu; appartements: ValeurLieu };
}

/** Name and https link of the BAN source, for the `i` button of the identity line. */
export interface SourceAdresse {
	nom: string;
	url: string | null;
}

/** One outcome per served answer; the page renders exactly one state for each (SEM-01). */
export type IssueFicheAdresse =
	| { kind: 'ok'; fiche: FicheAdressePublique; source: SourceAdresse | null }
	| { kind: 'hors_couverture'; fiche: FicheAdressePublique }
	| { kind: 'inconnue' }
	| { kind: 'voie' }
	| { kind: 'panne'; panne: DetailPanne };

type Brut = Record<string, unknown> | null | undefined;

const texte = (value: unknown): string | null => (typeof value === 'string' ? value : null);

function projeterFiche(donnees: Brut): FicheAdressePublique {
	const identite = (donnees?.identite ?? {}) as Record<string, unknown>;
	const commune = (identite.commune ?? {}) as Record<string, unknown>;
	const departement = (identite.departement ?? {}) as Record<string, unknown>;
	const parcelle = identite.parcelle as Brut;
	const couverture = (donnees?.couverture ?? {}) as Record<string, unknown>;
	const etatParcelle = texte(parcelle?.etat) as EtatParcelle | null;
	return {
		identite: {
			etat: identite.etat === 'indeterminee' ? 'indeterminee' : 'etablie',
			ban_id: texte(identite.ban_id) ?? '',
			libelle: texte(identite.libelle),
			numero: texte(identite.numero),
			rep: texte(identite.rep),
			nom_voie: texte(identite.nom_voie),
			code_postal: texte(identite.code_postal),
			commune: { code_insee: texte(commune.code_insee) ?? '', nom: texte(commune.nom) },
			departement: { code: texte(departement.code) ?? '', nom: texte(departement.nom) },
			parcelle: etatParcelle
				? {
						etat: etatParcelle,
						id_parcelle: etatParcelle === 'unique' ? texte(parcelle?.id_parcelle) : null,
						nombre_candidats: nombre(parcelle?.nombre_candidats),
						...(etatParcelle === 'indeterminee' ? { motif: texte(parcelle?.motif) } : {})
					}
				: null
		},
		couverture: {
			etat: couverture.etat === 'hors_couverture' ? 'hors_couverture' : 'heberge',
			motif: (texte(couverture.motif) as OutOfCoverageReason | null) ?? null
		},
		reperes: projeterReperes(donnees?.reperes as Brut),
		themes: projeterThemes(donnees?.themes)
	};
}

const CLES_THEMES: readonly CleThemeLieu[] = ['batiment_parcelle', 'risques', 'urbanisme', 'reseaux_transports'];
const ETATS_THEME: readonly ThemeLieu['etat'][] = ['value', 'not_available', 'out_of_coverage', 'temporary_error'];

function projeterElement(brut: Brut): ElementLieu | null {
	const libelle = texte(brut?.libelle);
	return libelle ? { libelle, distance_m: nombre(brut?.distance_m), nature: texte(brut?.nature) } : null;
}

/** One served value of a theme, picked field by field like `projeterValeur`. */
function projeterValeurTheme(brut: Brut): ValeurTheme {
	const base = projeterValeur(brut);
	const valeur = brut?.valeur;
	const elements = Array.isArray(brut?.elements) ? (brut.elements as Brut[]) : [];
	return {
		...base,
		valeur: typeof valeur === 'boolean' ? valeur : base.valeur,
		elements: elements.map(projeterElement).filter((element): element is ElementLieu => element !== null),
		liste_incomplete: brut?.liste_incomplete === true,
		rayon_m: nombre(brut?.rayon_m)
	};
}

/**
 * The served themes, in the served order. A theme with an unknown key or state is dropped (it has
 * no honest wording); every served state, with or without value, is kept (R06-09/6).
 */
function projeterThemes(brut: unknown): ThemeLieu[] {
	if (!Array.isArray(brut)) return [];
	return (brut as Brut[]).flatMap((theme): ThemeLieu[] => {
		const cle = texte(theme?.cle) as CleThemeLieu | null;
		const etat = texte(theme?.etat) as ThemeLieu['etat'] | null;
		if (!cle || !CLES_THEMES.includes(cle) || !etat || !ETATS_THEME.includes(etat)) return [];
		const groupes = Array.isArray(theme?.groupes) ? (theme.groupes as Brut[]) : [];
		return [
			{
				cle,
				titre: texte(theme?.titre) ?? '',
				applicable: theme?.applicable !== false,
				etat,
				motif: texte(theme?.motif),
				groupes: groupes.map((groupe) => ({
					cle: texte(groupe?.cle) ?? '',
					titre: texte(groupe?.titre),
					champs: (Array.isArray(groupe?.champs) ? (groupe.champs as Brut[]) : []).map(projeterValeurTheme)
				})),
				liste_incomplete: theme?.liste_incomplete === true
			}
		];
	});
}

const nombre = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);

function projeterSourceValeur(brut: Brut): SourceValeur | null {
	const nom = texte(brut?.nom);
	if (!nom) return null;
	return { cle: texte(brut?.cle) ?? '', nom, producteur: texte(brut?.producteur), url: texte(brut?.url) };
}

function projeterReference(brut: Brut): ReferenceCommune | null {
	if (!brut) return null;
	const territoire = (brut.territoire ?? {}) as Record<string, unknown>;
	return {
		valeur: nombre(brut.valeur),
		unite: texte(brut.unite) ?? '',
		territoire: { code_insee: texte(territoire.code_insee) ?? '', nom: texte(territoire.nom) },
		source: projeterSourceValeur(brut.source as Brut),
		millesime: texte(brut.millesime),
		url: texte(brut.url)
	};
}

/**
 * One served value of the place, picked field by field (no other member crosses the SSR model).
 * Also read by the document page (04-04), whose values have the same served shape.
 */
export function projeterValeur(brut: Brut): ValeurLieu {
	const valeur = brut?.valeur;
	return {
		field_id: texte(brut?.field_id) ?? '',
		// A value without a state is not a value: it is rendered as an outage of its landmark.
		etat: (texte(brut?.etat) as EtatValeurLieu | null) ?? 'temporary_error',
		valeur: typeof valeur === 'number' || typeof valeur === 'string' ? valeur : null,
		unite: texte(brut?.unite),
		periode: texte(brut?.periode),
		motif: texte(brut?.motif),
		source: projeterSourceValeur(brut?.source as Brut),
		rattachement: texte(brut?.rattachement) as Rattachement | null,
		reference: projeterReference(brut?.reference as Brut)
	};
}

function projeterSelectionne(brut: Brut): DocumentSelectionne {
	return {
		numero: texte(brut?.numero) ?? '',
		etiquette_dpe: texte(brut?.etiquette_dpe),
		etiquette_ges: texte(brut?.etiquette_ges),
		date_etablissement: texte(brut?.date_etablissement)
	};
}

function projeterSelection(brut: Brut): SelectionPlusRecent | null {
	const etat = texte(brut?.etat);
	if (etat !== 'unique' && etat !== 'egalite' && etat !== 'indeterminee') return null;
	const exAequo = Array.isArray(brut?.documents_ex_aequo) ? (brut.documents_ex_aequo as Brut[]) : [];
	return {
		etat,
		motif: texte(brut?.motif) ?? '',
		date: texte(brut?.date),
		document: brut?.document ? projeterSelectionne(brut.document as Brut) : null,
		documents_ex_aequo: exAequo.map(projeterSelectionne)
	};
}

function projeterReperes(brut: Brut): ReperesAdresse | null {
	if (!brut) return null;
	const ventes = (brut.ventes ?? {}) as Record<string, unknown>;
	const reference = (brut.reference_commune ?? {}) as Record<string, unknown>;
	return {
		dpe: { ...projeterValeur(brut.dpe as Brut), selection: projeterSelection((brut.dpe as Brut)?.selection as Brut) },
		ventes: { nombre: projeterValeur(ventes.nombre as Brut), derniere: projeterValeur(ventes.derniere as Brut) },
		argiles: projeterValeur(brut.argiles as Brut),
		reference_commune: {
			maisons: projeterValeur(reference.maisons as Brut),
			appartements: projeterValeur(reference.appartements as Brut)
		}
	};
}

/** First served source (the BAN for this route), name and URL only. */
function projeterSource(meta: ReponseApi<unknown>['meta']): SourceAdresse | null {
	const brut = meta?.source;
	const premiere = (Array.isArray(brut) ? brut[0] : brut) as Brut;
	const nom = texte(premiere?.nom);
	return nom ? { nom, url: texte(premiere?.url) } : null;
}

/** The `code` member of a served problem (RFC 9457 extension of 04-02), read before `api()` throws. */
async function codeDuProbleme(res: Response): Promise<string | null> {
	try {
		const body = (await res.clone().json()) as Brut;
		return texte(body?.code);
	} catch {
		return null;
	}
}

/**
 * Identity of one BAN key, as a typed outcome. A 404 is read by its served `code` (unknown key or
 * street without number); a 5xx or a network failure is an outage; a 409, 429, 401 or 403 is typed
 * by `detailPanne`, never worded as an outage. Server-side only (the page loader): the projected
 * model is what crosses the SSR serialization.
 */
export async function getFicheAdresse(
	fetchImpl: typeof fetch,
	banId: string
): Promise<IssueFicheAdresse> {
	const probleme: { code: string | null } = { code: null };
	// Same Response object handed back to `api()`: its auth-mode mark (client.ts) is kept.
	const fetchLisantLeCode: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) probleme.code = await codeDuProbleme(res);
		return res;
	};
	try {
		const res = await api<ReponseApi<Brut>>(
			`/v1/adresse/${encodeURIComponent(banId)}`,
			{},
			fetchLisantLeCode
		);
		const fiche = projeterFiche(res.donnees);
		if (fiche.couverture.etat === 'hors_couverture') return { kind: 'hors_couverture', fiche };
		return { kind: 'ok', fiche, source: projeterSource(res.meta) };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 404 && probleme.code === 'adresse_inconnue') return { kind: 'inconnue' };
		if (panne.statut === 404 && probleme.code === 'voie_sans_numero') return { kind: 'voie' };
		return { kind: 'panne', panne };
	}
}

// --- Report (`GET /v1/adresse/{ban_id}/rapport`, 04.7-01) --------------------------------------

/** The report of one BAN key, with the identity projected like the fiche's (one upstream read). */
export type IssueRapportAdresse =
	| { kind: 'ok'; fiche: FicheAdressePublique; rapport: RapportPublic; source: SourceAdresse | null }
	| { kind: 'hors_couverture'; fiche: FicheAdressePublique; rapport: RapportPublic }
	| { kind: 'inconnue' }
	| { kind: 'voie' }
	| { kind: 'panne'; panne: DetailPanne };

/**
 * R-07: `courte` serves the report after a short wait, the themes still read said `en_cours`;
 * `complete` waits for those reads. Without it, the API waits for every read as it always did.
 */
export type AttenteRapport = 'courte' | 'complete';

/**
 * The address report as a typed outcome, read like `getFicheAdresse`: the same problems (404 by its
 * served `code`, 5xx or network as an outage, 409/429/401/403 typed by `detailPanne`). The identity
 * comes from `donnees.adresse`, so the page reads the address once per visit. Server-side only.
 */
export async function getRapportAdresse(
	fetchImpl: typeof fetch,
	banId: string,
	attente?: AttenteRapport,
	options: RequestInit = {}
): Promise<IssueRapportAdresse> {
	const probleme: { code: string | null } = { code: null };
	const fetchLisantLeCode: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) probleme.code = await codeDuProbleme(res);
		return res;
	};
	try {
		const suffixe = attente ? `?attente=${attente}` : '';
		const res = await api<ReponseApi<Brut>>(`/v1/adresse/${encodeURIComponent(banId)}/rapport${suffixe}`, options, fetchLisantLeCode);
		const donnees = res.donnees;
		const fiche = projeterFiche({ identite: donnees?.adresse, couverture: donnees?.couverture });
		const rapport = projeterRapport(donnees);
		if (fiche.couverture.etat === 'hors_couverture') return { kind: 'hors_couverture', fiche, rapport };
		return { kind: 'ok', fiche, rapport, source: projeterSource(res.meta) };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 404 && probleme.code === 'adresse_inconnue') return { kind: 'inconnue' };
		if (panne.statut === 404 && probleme.code === 'voie_sans_numero') return { kind: 'voie' };
		return { kind: 'panne', panne };
	}
}

// --- Declared documents (`GET /v1/adresse/{ban_id}/dpe`, 04-03, D-109, D-111) ------------------

/** Closed list of the served badges of a document card (04-03). */
export type BadgeDocument =
	| 'plus_recent'
	| 'meme_date_plus_recente'
	| 'ancienne_methode'
	| 'expire'
	| 'remplace'
	| 'remplacement_incertain'
	| 'dates_incoherentes'
	| 'etablissement_futur';

/**
 * Address typed in a document, as published by the API (P-ADR-01, LIEU-03): `publiee` carries the
 * text as typed; `masquee` carries only its reason (the raw text is kept by DataCommune and never
 * served); `absente` when the document has none. Served only when the typed address is not
 * strictly concordant with the BAN one.
 */
export interface AdresseDeclaree {
	etat: 'publiee' | 'masquee' | 'absente';
	texte: string | null;
	motif: string | null;
}

/** One declared document of the list: the source number, never a reference (LIEU-03). */
export interface DocumentDpeCarte {
	numero: string;
	etiquette_dpe: string | null;
	etiquette_ges: string | null;
	type_batiment: string | null;
	surface_habitable: number | null;
	date_etablissement: string | null;
	date_fin_validite: string | null;
	/** Served badges, in the served order. A key outside the closed list is kept, never dropped. */
	badges: string[];
	/** Line 4 of the card; `null` for a strictly concordant document. */
	adresse_declaree: AdresseDeclaree | null;
}

/** Served signal of several dwellings on the parcel of the point (LIEU-02). */
export interface SignalPlusieursLogements {
	etat: 'value' | 'temporary_error';
	motif: string | null;
	nombre_lots_habitation: number | null;
	source: SourceValeur | null;
}

/**
 * D-110 finding served by the API only when every criterion holds (DPE-01, P-D110-NULL): its
 * criteria and sources. The page renders it from this field only and never derives it.
 */
export interface ConstatD110 {
	criteres: string[];
	sources: SourceValeur[];
}

/**
 * A document attached to the address by its exact raw address (04.1-13, D-127): its ADEME key is
 * absent from the BAN, its typed address names this address and only it. The served attachment is
 * kept as served (`adresse_brute_exacte` today); never a document of the ADEME key, never a dwelling.
 */
export interface DocumentDpeCarteAdresseBrute extends DocumentDpeCarte {
	rattachement: string | null;
}

/**
 * The group of the documents attached by their exact raw address, served apart from the list of the
 * ADEME key and outside its total, distribution, selection and D-110 finding (D45-05, D45-06).
 * `not_available`: the served generation was imported without the attachment; its total is null,
 * never 0. The cards are the most recent of the group (ten at most), the same on every page.
 */
export interface GroupeAdresseBrute {
	etat: 'value' | 'not_available';
	total: number | null;
	documents: DocumentDpeCarteAdresseBrute[];
}

export const CLASSES_DISTRIBUTION = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'unknown'] as const;
export type CleDistribution = (typeof CLASSES_DISTRIBUTION)[number];

/** Chronological list of the DPE declared at an address, under one served snapshot. */
export interface DocumentsDpeAdresse {
	etat: 'value' | 'out_of_coverage';
	motif: OutOfCoverageReason | null;
	source: SourceValeur | null;
	total: number | null;
	page: number;
	pages: number | null;
	par_page: number;
	/** Snapshot token carried by the page links (`?page=2&g=…`). */
	generation: string | null;
	distribution: Record<CleDistribution, number> | null;
	date_import: string | null;
	documents: DocumentDpeCarte[];
	selection: SelectionPlusRecent | null;
	signal_plusieurs_logements: SignalPlusieursLogements | null;
	/** `null`: no finding served (a criterion is false, unknown or unreadable). */
	constat_d110: ConstatD110 | null;
	/**
	 * Group of the documents attached by their exact raw address (04.1-13), never merged into the
	 * fields above. `null` (or absent in a hand-built list): not served — an answer before 2026-10-19,
	 * a list out of coverage, an unreadable state.
	 */
	adresseBrute?: GroupeAdresseBrute | null;
}

/**
 * One outcome per served answer of the list. `page_invalide` (404 `page_hors_limites`, 409
 * `generation_changee`) sends the loader back to page 1; every other failure is an outage or a
 * typed refusal of the section, never an empty list (SEM-01).
 */
export type IssueDocumentsDpe =
	| { kind: 'ok'; documents: DocumentsDpeAdresse }
	| { kind: 'page_invalide' }
	| { kind: 'panne'; panne: DetailPanne };

function projeterCarte(brut: Brut): DocumentDpeCarte {
	return {
		numero: texte(brut?.numero) ?? '',
		etiquette_dpe: texte(brut?.etiquette_dpe),
		etiquette_ges: texte(brut?.etiquette_ges),
		type_batiment: texte(brut?.type_batiment),
		surface_habitable: nombre(brut?.surface_habitable),
		date_etablissement: texte(brut?.date_etablissement),
		date_fin_validite: texte(brut?.date_fin_validite),
		badges: Array.isArray(brut?.badges) ? (brut.badges as unknown[]).filter((b): b is string => typeof b === 'string') : [],
		adresse_declaree: projeterAdresseDeclaree(brut?.adresse_declaree as Brut)
	};
}

/**
 * Only the three served fields are kept (no other key of the answer reaches the page or its
 * serialized data). A text is kept for `publiee` only; an unreadable state keeps no text.
 */
export function projeterAdresseDeclaree(brut: Brut): AdresseDeclaree | null {
	if (!brut) return null;
	const etat = brut.etat === 'publiee' || brut.etat === 'absente' ? brut.etat : 'masquee';
	return { etat, texte: etat === 'publiee' ? texte(brut.texte) : null, motif: etat === 'masquee' ? texte(brut.motif) : null };
}

function projeterSignal(brut: Brut): SignalPlusieursLogements | null {
	if (!brut) return null;
	return {
		// An unreadable state is an outage of the signal, never a served signal (I-3).
		etat: brut.etat === 'value' ? 'value' : 'temporary_error',
		motif: texte(brut.motif),
		nombre_lots_habitation: nombre(brut.nombre_lots_habitation),
		source: projeterSourceValeur(brut.source as Brut)
	};
}

/** A finding without a readable list of criteria is not a finding: nothing is rendered. */
function projeterConstat(brut: Brut): ConstatD110 | null {
	if (!brut || !Array.isArray(brut.criteres) || brut.criteres.length === 0) return null;
	return {
		criteres: (brut.criteres as unknown[]).filter((c): c is string => typeof c === 'string'),
		sources: Array.isArray(brut.sources)
			? (brut.sources as Brut[]).map(projeterSourceValeur).filter((s): s is SourceValeur => s !== null)
			: []
	};
}

function projeterDistribution(brut: Brut): Record<CleDistribution, number> | null {
	if (!brut) return null;
	return Object.fromEntries(CLASSES_DISTRIBUTION.map((cle) => [cle, nombre(brut[cle]) ?? 0])) as Record<
		CleDistribution,
		number
	>;
}

/**
 * The raw-address group as served: its state, its total kept as served (a null total stays null)
 * and its cards in the served order, each with its served attachment. No state served, or a state
 * outside the contract, is no group: nothing is displayed rather than a zero or a guess.
 */
function projeterGroupeAdresseBrute(brut: Brut): GroupeAdresseBrute | null {
	const etat = brut?.etat_adresse_brute;
	if (etat !== 'value' && etat !== 'not_available') return null;
	const cartes = Array.isArray(brut?.documents_adresse_brute) ? (brut.documents_adresse_brute as Brut[]) : [];
	return {
		etat,
		total: nombre(brut?.total_adresse_brute),
		documents: cartes.map((carte) => ({ ...projeterCarte(carte), rattachement: texte(carte?.rattachement) }))
	};
}

function projeterDocuments(brut: Brut): DocumentsDpeAdresse {
	return {
		etat: brut?.etat === 'out_of_coverage' ? 'out_of_coverage' : 'value',
		motif: (texte(brut?.motif) as OutOfCoverageReason | null) ?? null,
		source: projeterSourceValeur(brut?.source as Brut),
		total: nombre(brut?.total),
		page: nombre(brut?.page) ?? 1,
		pages: nombre(brut?.pages),
		par_page: nombre(brut?.par_page) ?? 10,
		generation: texte(brut?.generation),
		distribution: projeterDistribution(brut?.distribution as Brut),
		date_import: texte(brut?.date_import),
		documents: Array.isArray(brut?.documents) ? (brut.documents as Brut[]).map(projeterCarte) : [],
		selection: projeterSelection(brut?.selection as Brut),
		signal_plusieurs_logements: projeterSignal(brut?.signal_plusieurs_logements as Brut),
		constat_d110: projeterConstat(brut?.constat_d110 as Brut),
		adresseBrute: projeterGroupeAdresseBrute(brut)
	};
}

/**
 * One page of the documents declared at a BAN key. `page` and `g` are relayed as read in the URL:
 * the API validates them (T-04-18-01); the generation token is sent only when the link carries it.
 */
/**
 * Form of a BAN key accepted by `GET /v1/adresse/{ban_id}` (`BAN_ID_PATTERN`, at most 40
 * characters): the street form included (it has its own 404), any other form is unknown.
 */
export const BAN_ID_RE = /^(?=.{1,40}$)[0-9][0-9AB][0-9]{3}_[0-9A-Za-z]{4,6}(?:_[0-9]{5}(?:_[^\s/\\\x00-\x1F\x7F]{1,10})?)?$/;

/** Dependency of the list of `/adresse/[id]`, invalidated alone by « Recharger les documents DPE ». */
export const DEPENDANCE_DOCUMENTS = 'app:adresse-documents';

/** Bounds of `GET /v1/adresse/{ban_id}/dpe` (`page`: 1..100000, `g`: 16 hexadecimal digits). */
const PAGE_RE = /^[1-9]\d{0,5}$/;
const PAGE_MAX = 100_000;
const GENERATION_RE = /^[0-9a-f]{16}$/;

/**
 * True when the API would accept the page and the token of the URL (FRONT WR-02 of the D-15
 * review); `null` is an absent parameter. Never a loop: `?page=1` is valid.
 */
export function parametresDePageValides(page: string | null, generation: string | null): boolean {
	const pageValide = page === null || (PAGE_RE.test(page) && Number(page) <= PAGE_MAX);
	return pageValide && (generation === null || GENERATION_RE.test(generation));
}

export async function getDocumentsDpe(
	fetchImpl: typeof fetch,
	banId: string,
	page: string,
	g: string | null
): Promise<IssueDocumentsDpe> {
	const probleme: { code: string | null } = { code: null };
	const fetchLisantLeCode: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) probleme.code = await codeDuProbleme(res);
		return res;
	};
	const sp = new URLSearchParams({ page });
	if (g !== null) sp.set('g', g);
	try {
		const res = await api<ReponseApi<Brut>>(
			`/v1/adresse/${encodeURIComponent(banId)}/dpe?${sp.toString()}`,
			{},
			fetchLisantLeCode
		);
		return { kind: 'ok', documents: projeterDocuments(res.donnees) };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 404 && probleme.code === 'page_hors_limites') return { kind: 'page_invalide' };
		if (panne.statut === 409 && probleme.code === 'generation_changee') return { kind: 'page_invalide' };
		return { kind: 'panne', panne };
	}
}

// --- Sales of the parcel of the point (`GET /v1/adresse/{ban_id}/ventes`, 04-05, D-115) --------

/** Locals of one mutation as declared in DVF (counts, never a dwelling). */
export interface CompositionVente {
	maisons: number;
	appartements: number;
	dependances: number;
	locaux_activite: number;
}

/** Why the API withholds the price of a sale (DVF reuse restrictions). */
export type MotifMinimisation = 'vente_isolee_recente' | 'isolement_indetermine';

/**
 * One DVF mutation, attached to no dwelling and carrying no identifier. `prix` and `prix_m2` are
 * null when the API withholds the price (`prix_affiche` false, `minimisation` served): the exact
 * amount never reaches the page.
 */
export interface CarteVente {
	date: string;
	nature: string | null;
	composition: CompositionVente;
	surface_batie: number | null;
	prix: number | null;
	prix_m2: number | null;
	prix_affiche: boolean;
	minimisation: { motif: MotifMinimisation | string } | null;
	distance_m: number | null;
	/**
	 * Served mark of a VEFA (« Vente en l'état futur d'achèvement », 4.1-15): listed, never counted in
	 * any statistic (D-12). False for a sale, and when the API serves anything but `true`.
	 */
	hors_stats_vefa: boolean;
}

/** Sales of the parcel that contains the BAN point, one served page of a served generation. */
export interface VentesParcelle {
	etat: 'value' | 'not_available' | 'out_of_coverage' | 'temporary_error';
	motif: string | null;
	/** Served number of parcels touched by the point (`parcelles_multiples`). */
	nombre_parcelles: number | null;
	source: SourceValeur | null;
	periode: string | null;
	total: number | null;
	page: number;
	pages: number | null;
	par_page: number;
	/** DVF generation token carried by the page links (`ventes_page=2&vg=…`). */
	generation: string | null;
	ventes: CarteVente[];
}

/**
 * One outcome per served answer of the sales list. `page_invalide` (404 `page_hors_limites`, 409
 * `generation_changee`) sends the loader back to the first page; every other failure is an outage
 * or a typed refusal of the section, never an empty list (SEM-01).
 */
export type IssueVentes =
	| { kind: 'ok'; ventes: VentesParcelle }
	| { kind: 'page_invalide' }
	| { kind: 'panne'; panne: DetailPanne };

/** Dependency of the sales sections of `/adresse/[id]`, invalidated by « Recharger les ventes ». */
export const DEPENDANCE_VENTES = 'app:adresse-ventes';

const entier = (value: unknown): number => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0);

/** A withheld price stays withheld: an amount served next to a minimisation is never kept. */
export function projeterCarteVente(brut: Brut): CarteVente {
	const composition = (brut?.composition ?? {}) as Record<string, unknown>;
	const minimisation = brut?.minimisation as Brut;
	const motif = texte(minimisation?.motif);
	const affiche = brut?.prix_affiche === true && !motif;
	return {
		date: texte(brut?.date) ?? '',
		nature: texte(brut?.nature),
		composition: {
			maisons: entier(composition.maisons),
			appartements: entier(composition.appartements),
			dependances: entier(composition.dependances),
			locaux_activite: entier(composition.locaux_activite)
		},
		surface_batie: nombre(brut?.surface_batie),
		prix: affiche ? nombre(brut?.prix) : null,
		prix_m2: affiche ? nombre(brut?.prix_m2) : null,
		prix_affiche: affiche,
		// A price that is not displayable without a served reason is still withheld.
		minimisation: affiche ? null : { motif: motif ?? 'isolement_indetermine' },
		distance_m: nombre(brut?.distance_m),
		hors_stats_vefa: brut?.hors_stats_vefa === true
	};
}

const ETATS_VENTES = new Set(['value', 'not_available', 'out_of_coverage', 'temporary_error']);

function projeterVentes(brut: Brut): VentesParcelle {
	const etat = texte(brut?.etat);
	return {
		// An unreadable state is an outage of the section, never a served list.
		etat: etat && ETATS_VENTES.has(etat) ? (etat as VentesParcelle['etat']) : 'temporary_error',
		motif: texte(brut?.motif),
		nombre_parcelles: nombre(brut?.nombre_parcelles),
		source: projeterSourceValeur(brut?.source as Brut),
		periode: texte(brut?.periode),
		total: nombre(brut?.total),
		page: nombre(brut?.page) ?? 1,
		pages: nombre(brut?.pages),
		par_page: nombre(brut?.par_page) ?? 10,
		generation: texte(brut?.generation),
		ventes: Array.isArray(brut?.ventes) ? (brut.ventes as Brut[]).map(projeterCarteVente) : []
	};
}

/**
 * One page of the sales of the parcel of the point. `page` and `vg` are relayed as read in the URL
 * (the loader checks their form first; the API validates them again).
 */
export async function getVentesParcelle(
	fetchImpl: typeof fetch,
	banId: string,
	page: string,
	vg: string | null
): Promise<IssueVentes> {
	const probleme: { code: string | null } = { code: null };
	const fetchLisantLeCode: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) probleme.code = await codeDuProbleme(res);
		return res;
	};
	const sp = new URLSearchParams({ page });
	if (vg !== null) sp.set('vg', vg);
	try {
		const res = await api<ReponseApi<Brut>>(
			`/v1/adresse/${encodeURIComponent(banId)}/ventes?${sp.toString()}`,
			{},
			fetchLisantLeCode
		);
		return { kind: 'ok', ventes: projeterVentes(res.donnees) };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 404 && probleme.code === 'page_hors_limites') return { kind: 'page_invalide' };
		if (panne.statut === 409 && probleme.code === 'generation_changee') return { kind: 'page_invalide' };
		return { kind: 'panne', panne };
	}
}

// --- Sales declared at the address (`GET /v1/adresse/{ban_id}/ventes-declarees`, C37) ---------

/**
 * Sales whose declared DVF address is the address of the page: a relation of its own, never merged
 * with the sales of the parcel. `value` with `total` 0: no sale attached, not a zero of the source
 * (C44: first line of a sale only, sales without a BAN address); `not_available` carries its
 * served reason (`suffixe_sans_correspondance`, `suffixe_non_importe` under the number and suffix
 * rule; `suffixe_non_distingue` of the number-only rule; `sans_numero`, `adresse_indeterminee`).
 */
export interface VentesAdresse {
	etat: 'value' | 'not_available' | 'out_of_coverage';
	motif: string | null;
	source: SourceValeur | null;
	periode: string | null;
	total: number | null;
	/**
	 * C4 (04.5-07): under `vefa=incluses`, `total` counts the listed mutations, VEFA included, and
	 * `nombre_vefa` the VEFA among them, as served; null when it is not served (VEFA not searched) or
	 * not a count. Never recomputed from the cards of a page (D45-05).
	 */
	nombre_vefa: number | null;
	page: number;
	pages: number | null;
	par_page: number;
	/** The DVF generation token of the parcel list (`vg`), shared by every sales list. */
	generation: string | null;
	ventes: CarteVente[];
}

/** One outcome per served answer of the declared sales, as the sales of the parcel. */
export type IssueVentesAdresse =
	| { kind: 'ok'; ventes: VentesAdresse }
	| { kind: 'page_invalide' }
	| { kind: 'panne'; panne: DetailPanne };

const ETATS_VENTES_ADRESSE = new Set(['value', 'not_available', 'out_of_coverage']);

function projeterVentesAdresse(brut: Brut): VentesAdresse | null {
	const etat = texte(brut?.etat);
	// An unreadable state is an outage of the section, never a served list.
	if (!etat || !ETATS_VENTES_ADRESSE.has(etat)) return null;
	return {
		etat: etat as VentesAdresse['etat'],
		motif: texte(brut?.motif),
		source: projeterSourceValeur(brut?.source as Brut),
		periode: texte(brut?.periode),
		total: nombre(brut?.total),
		nombre_vefa: Number.isInteger(brut?.nombre_vefa) && (brut?.nombre_vefa as number) >= 0 ? (brut?.nombre_vefa as number) : null,
		page: nombre(brut?.page) ?? 1,
		pages: nombre(brut?.pages),
		par_page: nombre(brut?.par_page) ?? 10,
		generation: texte(brut?.generation),
		ventes: Array.isArray(brut?.ventes) ? (brut.ventes as Brut[]).map(projeterCarteVente) : []
	};
}

/**
 * One page of the sales declared at the address; `page` (`vd_page`) and `vg` relayed as read. C4
 * (04.5-07, R8): the VEFA are asked for (`vefa=incluses`), listed and marked, never counted. C3
 * (04.5-08, R8): by number and suffix (`regle=numero_et_suffixe`), on a DVF generation proven to keep
 * the suffix on both instances (04.5-08-PREUVE.md); « 2 bis » shows its « 2 B » sales, never « 2 ».
 */
export async function getVentesAdresse(
	fetchImpl: typeof fetch,
	banId: string,
	page: string,
	vg: string | null
): Promise<IssueVentesAdresse> {
	const probleme: { code: string | null } = { code: null };
	const fetchLisantLeCode: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) probleme.code = await codeDuProbleme(res);
		return res;
	};
	const sp = new URLSearchParams({ page, vefa: 'incluses', regle: 'numero_et_suffixe' });
	if (vg !== null) sp.set('vg', vg);
	try {
		const res = await api<ReponseApi<Brut>>(
			`/v1/adresse/${encodeURIComponent(banId)}/ventes-declarees?${sp.toString()}`,
			{},
			fetchLisantLeCode
		);
		const ventes = projeterVentesAdresse(res.donnees);
		return ventes ? { kind: 'ok', ventes } : { kind: 'panne', panne: { statut: null, titre: null, message: null } };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 404 && probleme.code === 'page_hors_limites') return { kind: 'page_invalide' };
		if (panne.statut === 409 && probleme.code === 'generation_changee') return { kind: 'page_invalide' };
		return { kind: 'panne', panne };
	}
}

// --- Similar sales (`GET /v1/adresse/{ban_id}/ventes-ressemblantes`, 04-05, D-114) ------------

/** Criteria as applied by the API: the page words them as served, never the raw input. */
export interface CriteresAppliques {
	type: 'maison' | 'appartement';
	surface: number | null;
	tolerance: number;
	surface_min: number | null;
	surface_max: number | null;
	perimetre: 'rayon' | 'commune';
	rayon_m: number | null;
	periode: { annees: number; debut: number | null; fin: number | null };
}

/** One served page of the similar sales, its applied criteria and its count (`effectif`). */
export interface VentesRessemblantes {
	etat: 'value' | 'not_available' | 'out_of_coverage';
	motif: string | null;
	rattachement: 'rayon_du_point' | 'commune_de_l_adresse';
	source: SourceValeur | null;
	criteres_appliques: CriteresAppliques | null;
	effectif: number | null;
	page: number;
	pages: number | null;
	par_page: number;
	generation: string | null;
	ventes: CarteVente[];
	/** C16: the radius reaches a departement not hosted; `effectif` counts the hosted side only. */
	liste_incomplete: boolean;
}

/**
 * One outcome per state of the similar sales. `sans_type`: no type chosen, nothing is read (Q6);
 * `criteres_non_reconnus`: a criterion outside the closed lists, never widened (422 or refused by
 * the loader before any read); `page_invalide` sends the loader back to the first page.
 */
export type IssueComparables =
	| { kind: 'sans_type' }
	| { kind: 'criteres_non_reconnus' }
	| { kind: 'ok'; comparables: VentesRessemblantes }
	| { kind: 'page_invalide' }
	| { kind: 'panne'; panne: DetailPanne };

/** Criteria relayed to the API as read in the URL (already checked by `lireCriteres`). */
export interface CriteresRelayes {
	type: string;
	surface: string | null;
	tolerance: string | null;
	rayon: string | null;
	periode: string | null;
}

function projeterCriteres(brut: Brut): CriteresAppliques | null {
	const type = texte(brut?.type);
	if (type !== 'maison' && type !== 'appartement') return null;
	const periode = (brut?.periode ?? {}) as Record<string, unknown>;
	return {
		type,
		surface: nombre(brut?.surface),
		tolerance: nombre(brut?.tolerance) ?? 0,
		surface_min: nombre(brut?.surface_min),
		surface_max: nombre(brut?.surface_max),
		perimetre: brut?.perimetre === 'commune' ? 'commune' : 'rayon',
		rayon_m: nombre(brut?.rayon_m),
		periode: { annees: nombre(periode.annees) ?? 0, debut: nombre(periode.debut), fin: nombre(periode.fin) }
	};
}

function projeterComparables(brut: Brut): VentesRessemblantes {
	const etat = texte(brut?.etat);
	return {
		etat: etat === 'value' || etat === 'not_available' || etat === 'out_of_coverage' ? etat : 'not_available',
		motif: texte(brut?.motif),
		rattachement: brut?.rattachement === 'commune_de_l_adresse' ? 'commune_de_l_adresse' : 'rayon_du_point',
		source: projeterSourceValeur(brut?.source as Brut),
		criteres_appliques: projeterCriteres(brut?.criteres_appliques as Brut),
		effectif: nombre(brut?.effectif),
		page: nombre(brut?.page) ?? 1,
		pages: nombre(brut?.pages),
		par_page: nombre(brut?.par_page) ?? 10,
		generation: texte(brut?.generation),
		ventes: Array.isArray(brut?.ventes) ? (brut.ventes as Brut[]).map(projeterCarteVente) : [],
		liste_incomplete: brut?.liste_incomplete === true
	};
}

/** One page of the similar sales; only the criteria present in the URL are relayed. */
export async function getVentesRessemblantes(
	fetchImpl: typeof fetch,
	banId: string,
	criteres: CriteresRelayes,
	page: string,
	vg: string | null
): Promise<IssueComparables> {
	const probleme: { code: string | null } = { code: null };
	const fetchLisantLeCode: typeof fetch = async (input, init) => {
		const res = await fetchImpl(input, init);
		if (!res.ok) probleme.code = await codeDuProbleme(res);
		return res;
	};
	const sp = new URLSearchParams({ type: criteres.type });
	for (const cle of ['surface', 'tolerance', 'rayon', 'periode'] as const) {
		const valeur = criteres[cle];
		if (valeur !== null) sp.set(cle, valeur);
	}
	sp.set('page', page);
	if (vg !== null) sp.set('vg', vg);
	try {
		const res = await api<ReponseApi<Brut>>(
			`/v1/adresse/${encodeURIComponent(banId)}/ventes-ressemblantes?${sp.toString()}`,
			{},
			fetchLisantLeCode
		);
		return { kind: 'ok', comparables: projeterComparables(res.donnees) };
	} catch (err) {
		const panne = detailPanne(err);
		if (panne.statut === 422) return { kind: 'criteres_non_reconnus' };
		if (panne.statut === 404 && probleme.code === 'page_hors_limites') return { kind: 'page_invalide' };
		if (panne.statut === 409 && probleme.code === 'generation_changee') return { kind: 'page_invalide' };
		return { kind: 'panne', panne };
	}
}

// --- Addresses compared (`GET /v1/adresses/comparaison`, 04-07, CMP-01, D-116) -------------------

/**
 * Reason why no gap is served between two addresses (the 03-09 rule, `MotifNonCalculAdresse`); on
 * the rows of the designated documents (04-11), the reasons of the documents (04-10) as well.
 */
export type MotifNonCalculAdresse =
	| 'adresse_hors_couverture'
	| 'donnee_absente'
	| 'periode_inconnue'
	| 'periodes_differentes'
	| 'unites_differentes'
	| 'definitions_differentes'
	| 'non_numerique'
	| 'methodes_differentes'
	| 'methode_indeterminee'
	| 'adresse_declaree_non_affichee'
	| 'liste_incomplete';

export type CleGroupeComparaisonAdresse = 'documents' | 'ventes' | 'lieu' | 'commune_reference';

/**
 * The DPE the visitor designated on a compared address (04-11, D-102): kept by the API only when
 * declared at this address. `designe`: read (class and establishment date of the document page);
 * `temporary_error`: the DPE could not be read, the pair is kept; `out_of_coverage`: the DPE is
 * not hosted for the address. It lives in the link only and attaches the document to no dwelling.
 */
export interface DesignationDpe {
	numero: string;
	etat: 'designe' | 'temporary_error' | 'out_of_coverage';
	motif: string | null;
	classe_energie: string | null;
	date_etablissement: string | null;
}

/** A designated pair whose number is not declared at its address (« Lien altéré »). */
export interface DesignationEcartee {
	ban_id: string;
	numero: string;
}

/** A compared address: served identity and coverage, never a coordinate. */
export interface ColonneAdresse {
	ban_id: string;
	libelle: string | null;
	/** « {numéro}{indice} {voie} », null when the BAN rows of the key disagree on it. */
	numero_voie: string | null;
	code_postal: string | null;
	commune: { code_insee: string; nom: string | null };
	departement: DepartementRef;
	couverture: { etat: 'heberge' | 'hors_couverture'; motif: OutOfCoverageReason | null };
	identite_etat: 'etablie' | 'indeterminee';
	/** The designated DPE of this address, if any (04-11). */
	designation: DesignationDpe | null;
}

/** One address's value for a row (the value of its page) and the verdict the API served. */
export interface CelluleComparaisonAdresse extends ValeurTheme {
	ban_id: string;
	/** The served most-recent selection of ①, on the « most recent document » row only. */
	selection: SelectionPlusRecent | null;
	ecart: { valeur: number; unite: string } | null;
	motif_non_calcul: MotifNonCalculAdresse | null;
	verdict: 'identique' | 'different' | null;
	identique_a_la_reference: boolean | null;
}

export interface LigneComparaisonAdresse {
	cle: string;
	field_id: string;
	identiques: boolean;
	/** A row of the designated documents (04-11): the value of the document, attached to no dwelling. */
	designe: boolean;
	cellules: CelluleComparaisonAdresse[];
}

export interface GroupeComparaisonAdresse {
	cle: CleGroupeComparaisonAdresse;
	titre: string;
	lignes: LigneComparaisonAdresse[];
}

export interface ComparaisonAdresses {
	colonnes: ColonneAdresse[];
	groupes: GroupeComparaisonAdresse[];
	ids_ecartes: string[];
	/** The designated pairs whose number is not declared at their address (04-11). */
	designations_ecartees: DesignationEcartee[];
}

const CLES_GROUPES_COMPARAISON: readonly CleGroupeComparaisonAdresse[] = ['documents', 'ventes', 'lieu', 'commune_reference'];
const MOTIFS_NON_CALCUL_ADRESSE: readonly MotifNonCalculAdresse[] = [
	'adresse_hors_couverture',
	'donnee_absente',
	'periode_inconnue',
	'periodes_differentes',
	'unites_differentes',
	'definitions_differentes',
	'non_numerique',
	'methodes_differentes',
	'methode_indeterminee',
	'adresse_declaree_non_affichee',
	'liste_incomplete'
];

const ETATS_DESIGNATION: readonly DesignationDpe['etat'][] = ['designe', 'temporary_error', 'out_of_coverage'];

/** The served designation, picked member by member; an unreadable one is no designation. */
function projeterDesignation(brut: Brut): DesignationDpe | null {
	const numero = texte(brut?.numero);
	const etat = brut?.etat as DesignationDpe['etat'];
	if (!numero || !ETATS_DESIGNATION.includes(etat)) return null;
	return {
		numero,
		etat,
		motif: texte(brut?.motif),
		classe_energie: texte(brut?.classe_energie),
		date_etablissement: texte(brut?.date_etablissement)
	};
}

function projeterColonne(brut: Brut): ColonneAdresse {
	const commune = (brut?.commune ?? {}) as Record<string, unknown>;
	const departement = (brut?.departement ?? {}) as Record<string, unknown>;
	const couverture = (brut?.couverture ?? {}) as Record<string, unknown>;
	return {
		ban_id: texte(brut?.ban_id) ?? '',
		libelle: texte(brut?.libelle),
		numero_voie: texte(brut?.numero_voie),
		code_postal: texte(brut?.code_postal),
		commune: { code_insee: texte(commune.code_insee) ?? '', nom: texte(commune.nom) },
		departement: { code: texte(departement.code) ?? '', nom: texte(departement.nom) },
		couverture: {
			etat: couverture.etat === 'hors_couverture' ? 'hors_couverture' : 'heberge',
			motif: (texte(couverture.motif) as OutOfCoverageReason | null) ?? null
		},
		identite_etat: brut?.identite_etat === 'indeterminee' ? 'indeterminee' : 'etablie',
		designation: projeterDesignation(brut?.designation as Brut)
	};
}

function projeterCelluleComparee(brut: Brut): CelluleComparaisonAdresse {
	const ecart = brut?.ecart as Brut;
	const motif = texte(brut?.motif_non_calcul) as MotifNonCalculAdresse | null;
	const verdict = brut?.verdict;
	return {
		...projeterValeurTheme(brut),
		ban_id: texte(brut?.ban_id) ?? '',
		selection: projeterSelection(brut?.selection as Brut),
		ecart: ecart && nombre(ecart.valeur) !== null ? { valeur: nombre(ecart.valeur) as number, unite: texte(ecart.unite) ?? '' } : null,
		motif_non_calcul: motif && MOTIFS_NON_CALCUL_ADRESSE.includes(motif) ? motif : null,
		verdict: verdict === 'identique' || verdict === 'different' ? verdict : null,
		identique_a_la_reference: typeof brut?.identique_a_la_reference === 'boolean' ? brut.identique_a_la_reference : null
	};
}

/**
 * The served comparison, picked member by member: the groups in the served order (the commune
 * reference last), every cell as served. A group with an unknown key is dropped (no wording).
 */
export function projeterComparaisonAdresses(donnees: Brut): ComparaisonAdresses {
	const groupes = Array.isArray(donnees?.groupes) ? (donnees.groupes as Brut[]) : [];
	return {
		colonnes: (Array.isArray(donnees?.colonnes) ? (donnees.colonnes as Brut[]) : []).map(projeterColonne),
		groupes: groupes.flatMap((groupe): GroupeComparaisonAdresse[] => {
			const cle = texte(groupe?.cle) as CleGroupeComparaisonAdresse | null;
			if (!cle || !CLES_GROUPES_COMPARAISON.includes(cle)) return [];
			const lignes = Array.isArray(groupe?.lignes) ? (groupe.lignes as Brut[]) : [];
			return [
				{
					cle,
					titre: texte(groupe?.titre) ?? '',
					lignes: lignes.map((ligne) => ({
						cle: texte(ligne?.cle) ?? '',
						field_id: texte(ligne?.field_id) ?? '',
						identiques: ligne?.identiques === true,
						designe: ligne?.designe === true,
						cellules: (Array.isArray(ligne?.cellules) ? (ligne.cellules as Brut[]) : []).map(projeterCelluleComparee)
					}))
				}
			];
		}),
		ids_ecartes: (Array.isArray(donnees?.ids_ecartes) ? (donnees.ids_ecartes as unknown[]) : []).flatMap((id) =>
			typeof id === 'string' ? [id] : []
		),
		designations_ecartees: (Array.isArray(donnees?.designations_ecartees) ? (donnees.designations_ecartees as Brut[]) : []).flatMap(
			(paire): DesignationEcartee[] => {
				const banId = texte(paire?.ban_id);
				const numero = texte(paire?.numero);
				return banId && numero ? [{ ban_id: banId, numero }] : [];
			}
		)
	};
}

/**
 * Compare 2 to 4 addresses in one call; the first is the reference of the gaps. The API validates
 * the keys (422) and serves every value, gap, reason and verdict; a failure throws (`ApiError`).
 * `designes` (04-11): one designated DPE per address at most, `<ban_id>:<numero>`; the API keeps a
 * pair only when the number is declared at this address.
 */
export async function getComparaisonAdresses(
	ids: readonly string[],
	fetchImpl?: typeof fetch,
	designes: readonly { ban_id: string; numero: string }[] = []
): Promise<ComparaisonAdresses> {
	const sp = new URLSearchParams({ ids: ids.join(',') });
	if (designes.length) sp.set('designes', designes.map((paire) => `${paire.ban_id}:${paire.numero}`).join(','));
	const res = await api<ReponseApi<Brut>>(`/v1/adresses/comparaison?${sp}`, {}, fetchImpl);
	return projeterComparaisonAdresses(res.donnees);
}
