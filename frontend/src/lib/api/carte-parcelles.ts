// Client of the interactive parcel map of the address fiche (04.10-03; contract frozen by 04.10-02).
//
// The browser reads these routes itself, after the visitor opened the map, on the origin of the site
// (`/carte/…`, relayed to `/v1/carte/…`): no API key, no cookie, and never through a loader or an
// endpoint of SvelteKit, where the data would land in the served HTML and behind the quota of the
// fiche (Q7). The paths are constants of this module, checked against `contracts/openapi.v1.json`.
//
// An answer is one of three outcomes, never an exception to the screen: read, rate limited (429, with
// the served delay), or an outage. An answer that cannot be read whole is an outage: an unknown state,
// a parcel without geometry or without label point never becomes a shorter list (SEM-01).
import { projeterCarteVente, type CarteVente } from './adresses';
import { secondesRetryAfter } from './client';

export const ZOOM_MIN = 16;
export const ZOOM_MAX = 19;
export const ZOOM_OUVERTURE = 18;

export const CHEMIN_CONTEXTE = '/carte/{ban_id}/contexte';
export const CHEMIN_TUILE_PARCELLES = '/carte/parcelles/{z}/{x}/{y}';
/** Ground tiles (Plan IGN) relayed by our API: the browser never asks the IGN itself (04.10-04). */
export const CHEMIN_FOND = '/carte/fond/{z}/{x}/{y}.png';
/** The template the tile layer of Leaflet fills: the contract path itself, on the origin of the site. */
export const GABARIT_FOND = CHEMIN_FOND;
/** Sales of one parcel around the address of the fiche, five per page (04.10-05, read by 04.10-07). */
export const CHEMIN_VENTES_PARCELLE = '/carte/{ban_id}/parcelle/{id_parcelle}/ventes';
/** Every path this client asks; each is `/v1` + itself in the contract (tested). */
export const CHEMINS_CARTE = [CHEMIN_CONTEXTE, CHEMIN_TUILE_PARCELLES, CHEMIN_FOND, CHEMIN_VENTES_PARCELLE] as const;

export interface EtatParcellesCarte {
	/** `value`: every parcel of our base, zero included; a `motif` then says a part is not hosted. */
	etat: 'value' | 'out_of_coverage';
	motif: string | null;
}

export interface EtatVentesCarte {
	etat: 'value' | 'out_of_coverage' | 'temporary_error';
	motif: string | null;
	periode: string | null;
	annee_debut: number | null;
}

export interface ContexteCarte {
	centre: { lon: number; lat: number };
	emprise: { ouest: number; sud: number; est: number; nord: number };
	parcelle_adresse: {
		etat: 'value' | 'not_available' | 'out_of_coverage' | 'temporary_error';
		id_parcelle: string | null;
		motif: string | null;
		nombre_parcelles: number | null;
	};
	parcelles: EtatParcellesCarte;
	ventes: EtatVentesCarte;
	cadastre_millesime: string | null;
	fond: { attribution: string; licence: string };
}

type Anneau = [number, number][];

export interface ParcelleCarte {
	type: 'Feature';
	/** The parcel reference; a parcel across two tiles comes in both with the same one. */
	id: string;
	geometry: { type: 'Polygon'; coordinates: Anneau[] } | { type: 'MultiPolygon'; coordinates: Anneau[][] };
	properties: {
		id_parcelle: string;
		/** Null: the cadastre gives no surface. Never read as zero. */
		contenance: number | null;
		/** Served point inside the parcel, `[lon, lat]`. */
		etiquette: [number, number];
		/** Null: not counted (sales not read here). Never read as zero. */
		ventes: number | null;
	};
}

export interface TuileParcelles {
	parcelles: EtatParcellesCarte;
	ventes: EtatVentesCarte;
	cadastre_millesime: string | null;
	collection: { type: 'FeatureCollection'; features: ParcelleCarte[] };
}

/** One parcel of a sale that is in the cadastre and inside the bounds of the address: it can be drawn. */
export interface ParcelleDuGroupe {
	id_parcelle: string;
	geometry: ParcelleCarte['geometry'];
	etiquette: [number, number];
}

/**
 * The parcels sold together in one sale: a dated fact, never an owner. Every count and the surface are
 * served; the surface is null as soon as one parcel has none (never a partial sum). `parcelles` is null
 * beyond the cap of the route: the group is then said, never drawn.
 */
export interface GroupeVendu {
	/** Every parcel of the sale, the asked one included. */
	total: number;
	/** `total` minus one, served. */
	autres: number;
	absentes_du_cadastre: number;
	hors_emprise: number;
	/**
	 * Those in the cadastre without a surface (served since the review of 2026-11-15, R3). Null when a
	 * server before it does not serve it: unknown, never zero.
	 */
	sans_surface: number | null;
	surface_totale_m2: number | null;
	parcelles: ParcelleDuGroupe[] | null;
}

/** One served page of the sales of a parcel of the map. `groupes` follows `ventes` rank for rank. */
export interface VentesParcelleCarte {
	etat: 'value' | 'out_of_coverage';
	motif: string | null;
	periode: string | null;
	annee_debut: number | null;
	total: number | null;
	page: number;
	pages: number | null;
	par_page: number;
	/** DVF generation token, carried by the next page (`vg`). */
	generation: string | null;
	ventes: CarteVente[];
	groupes: (GroupeVendu | null)[];
}

export type IssueCarte<T> =
	| { kind: 'ok'; donnees: T }
	| { kind: 'limite'; retryAfter: number | null }
	| { kind: 'panne'; statut: number | null };

/**
 * The sales of a parcel add one outcome: a page that no longer exists (404 `page_hors_limites`) or a
 * generation that changed (409 `generation_changee`) sends the panel back to the first page.
 */
export type IssueVentesCarte = IssueCarte<VentesParcelleCarte> | { kind: 'page_invalide' };

export interface OptionsLecture {
	signal?: AbortSignal;
	fetchImpl?: typeof fetch;
}

type Brut = Record<string, unknown>;

const objet = (v: unknown): Brut | null => (v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Brut) : null);
const texte = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const nombre = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const compte = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null);

/** A parcel reference: commune (5), prefix (3), section (2), number (4). */
const ID_PARCELLE = /^[0-9A-Za-z]{14}$/;
const idParcelle = (v: unknown): string | null => (typeof v === 'string' && ID_PARCELLE.test(v) ? v : null);

/** A value that is either absent (null) or readable; `undefined` when served but unreadable. */
function nulOu<T>(v: unknown, lire: (v: unknown) => T | null): T | null | undefined {
	if (v === null || v === undefined) return null;
	return lire(v) ?? undefined;
}

function etatDe<E extends string>(v: unknown, connus: readonly E[]): E | null {
	return typeof v === 'string' && (connus as readonly string[]).includes(v) ? (v as E) : null;
}

function projeterEtatParcelles(brut: unknown): EtatParcellesCarte | null {
	const o = objet(brut);
	const etat = etatDe(o?.etat, ['value', 'out_of_coverage'] as const);
	return o && etat ? { etat, motif: texte(o.motif) } : null;
}

function projeterEtatVentes(brut: unknown): EtatVentesCarte | null {
	const o = objet(brut);
	const etat = etatDe(o?.etat, ['value', 'out_of_coverage', 'temporary_error'] as const);
	return o && etat ? { etat, motif: texte(o.motif), periode: texte(o.periode), annee_debut: nombre(o.annee_debut) } : null;
}

function position(v: unknown): [number, number] | null {
	if (!Array.isArray(v) || v.length !== 2) return null;
	const [lon, lat] = [nombre(v[0]), nombre(v[1])];
	return lon === null || lat === null ? null : [lon, lat];
}

function tous<T>(v: unknown, lire: (item: unknown) => T | null, minimum = 1): T[] | null {
	if (!Array.isArray(v) || v.length < minimum) return null;
	const lus: T[] = [];
	for (const item of v) {
		const lu = lire(item);
		if (lu === null) return null;
		lus.push(lu);
	}
	return lus;
}

// A closed ring has four positions at least.
const anneau = (v: unknown): Anneau | null => tous(v, position, 4);
const polygone = (v: unknown): Anneau[] | null => tous(v, anneau);

function projeterGeometrie(brut: unknown): ParcelleCarte['geometry'] | null {
	const g = objet(brut);
	if (g?.type === 'Polygon') {
		const coordinates = polygone(g.coordinates);
		return coordinates ? { type: 'Polygon', coordinates } : null;
	}
	if (g?.type === 'MultiPolygon') {
		const coordinates = tous(g.coordinates, polygone);
		return coordinates ? { type: 'MultiPolygon', coordinates } : null;
	}
	return null;
}

function projeterParcelle(brut: unknown): ParcelleCarte | null {
	const f = objet(brut);
	const p = objet(f?.properties);
	if (!f || !p || f.type !== 'Feature') return null;
	const id = idParcelle(p.id_parcelle);
	const geometry = projeterGeometrie(f.geometry);
	const etiquette = position(p.etiquette);
	const contenance = nulOu(p.contenance, (v) => (nombre(v) !== null && (v as number) >= 0 ? (v as number) : null));
	const ventes = nulOu(p.ventes, compte);
	if (!id || f.id !== id || !geometry || !etiquette || contenance === undefined || ventes === undefined) return null;
	return { type: 'Feature', id, geometry, properties: { id_parcelle: id, contenance, etiquette, ventes } };
}

function projeterTuile(brut: unknown): TuileParcelles | null {
	const d = objet(brut);
	const parcelles = projeterEtatParcelles(d?.parcelles);
	const ventes = projeterEtatVentes(d?.ventes);
	const collection = objet(d?.collection);
	if (!d || !parcelles || !ventes || collection?.type !== 'FeatureCollection') return null;
	// One unreadable parcel refuses the whole tile: a shorter list would read as fewer parcels.
	const features = tous(collection.features, projeterParcelle, 0);
	if (!features) return null;
	return { parcelles, ventes, cadastre_millesime: texte(d.cadastre_millesime), collection: { type: 'FeatureCollection', features } };
}

function projeterContexte(brut: unknown): ContexteCarte | null {
	const d = objet(brut);
	const centre = objet(d?.centre);
	const emprise = objet(d?.emprise);
	const pa = objet(d?.parcelle_adresse);
	const fond = objet(d?.fond);
	const parcelles = projeterEtatParcelles(d?.parcelles);
	const ventes = projeterEtatVentes(d?.ventes);
	if (!d || !centre || !emprise || !pa || !fond || !parcelles || !ventes) return null;

	const [lon, lat] = [nombre(centre.lon), nombre(centre.lat)];
	const [ouest, sud, est, nord] = [nombre(emprise.ouest), nombre(emprise.sud), nombre(emprise.est), nombre(emprise.nord)];
	if (lon === null || lat === null || ouest === null || sud === null || est === null || nord === null) return null;
	if (ouest >= est || sud >= nord) return null;

	const etat = etatDe(pa.etat, ['value', 'not_available', 'out_of_coverage', 'temporary_error'] as const);
	const id = nulOu(pa.id_parcelle, idParcelle);
	// `value` is the parcel that contains the point: without its reference the state is unreadable.
	if (!etat || id === undefined || (etat === 'value' && id === null)) return null;

	const [attribution, licence] = [texte(fond.attribution), texte(fond.licence)];
	if (!attribution || !licence) return null;

	return {
		centre: { lon, lat },
		emprise: { ouest, sud, est, nord },
		parcelle_adresse: { etat, id_parcelle: id, motif: texte(pa.motif), nombre_parcelles: nombre(pa.nombre_parcelles) },
		parcelles,
		ventes,
		cadastre_millesime: texte(d.cadastre_millesime),
		fond: { attribution, licence }
	};
}

function projeterParcelleDuGroupe(brut: unknown): ParcelleDuGroupe | null {
	const p = objet(brut);
	const id = idParcelle(p?.id_parcelle);
	const geometry = projeterGeometrie(p?.geometry);
	const etiquette = position(p?.etiquette);
	return id && geometry && etiquette ? { id_parcelle: id, geometry, etiquette } : null;
}

function projeterGroupe(brut: unknown): GroupeVendu | null {
	const g = objet(brut);
	if (!g) return null;
	const [total, autres, absentes, horsEmprise] = [compte(g.total), compte(g.autres), compte(g.absentes_du_cadastre), compte(g.hors_emprise)];
	const surface = nulOu(g.surface_totale_m2, compte);
	const parcelles = nulOu(g.parcelles, (v) => tous(v, projeterParcelleDuGroupe, 0));
	// Absent from an older server: unknown. Served but not a count: the answer is broken.
	const sansSurface = 'sans_surface' in g ? compte(g.sans_surface) : null;
	if ('sans_surface' in g && sansSurface === null) return null;
	if (total === null || autres === null || absentes === null || horsEmprise === null || surface === undefined || parcelles === undefined) return null;
	return { total, autres, absentes_du_cadastre: absentes, hors_emprise: horsEmprise, sans_surface: sansSurface, surface_totale_m2: surface, parcelles };
}

/** The groups of a page, null kept (a sale of the parcel alone); one unreadable group refuses them all. */
function projeterGroupes(v: unknown): (GroupeVendu | null)[] | null {
	if (!Array.isArray(v)) return null;
	const lus: (GroupeVendu | null)[] = [];
	for (const brut of v) {
		const groupe = brut === null ? null : projeterGroupe(brut);
		if (brut !== null && groupe === null) return null;
		lus.push(groupe);
	}
	return lus;
}

/** The cards of the fiche, with its price rule: a withheld price stays withheld (`projeterCarteVente`). */
const carteVente = (v: unknown): CarteVente | null => {
	const o = objet(v);
	return o ? projeterCarteVente(o) : null;
};

function projeterVentes(brut: unknown): VentesParcelleCarte | null {
	const d = objet(brut);
	const etat = etatDe(d?.etat, ['value', 'out_of_coverage'] as const);
	if (!d || !etat) return null;
	const page = compte(d.page);
	const parPage = compte(d.par_page);
	const total = nulOu(d.total, compte);
	const pages = nulOu(d.pages, compte);
	// Lists served absent are empty (out of coverage); served but unreadable, the answer is refused.
	const ventes = d.ventes === undefined ? [] : tous(d.ventes, carteVente, 0);
	const groupes = d.groupes === undefined && ventes?.length === 0 ? [] : projeterGroupes(d.groupes);
	if (!page || !parPage || total === undefined || pages === undefined || !ventes || !groupes) return null;
	// Rank for rank: a group that belongs to no card, or a card without its group, is unreadable.
	if (groupes.length !== ventes.length) return null;
	return {
		etat,
		motif: texte(d.motif),
		periode: texte(d.periode),
		annee_debut: nombre(d.annee_debut),
		total,
		page,
		pages,
		par_page: parPage,
		generation: texte(d.generation),
		ventes,
		groupes
	};
}

async function lire<T, R = never>(
	chemin: string,
	projeter: (donnees: unknown) => T | null,
	options: OptionsLecture,
	refus?: (statut: number, code: string | null) => R | null
): Promise<IssueCarte<T> | R> {
	const fetchImpl = options.fetchImpl ?? fetch;
	let reponse: Response;
	try {
		reponse = await fetchImpl(chemin, { signal: options.signal, credentials: 'omit', headers: { accept: 'application/json' } });
	} catch {
		return { kind: 'panne', statut: null };
	}
	if (reponse.status === 429) return { kind: 'limite', retryAfter: secondesRetryAfter(reponse) };
	if (!reponse.ok) {
		// A refusal the caller types by its served `code`; any other one is an outage.
		const code = refus ? texte(objet(await reponse.json().catch(() => null))?.code) : null;
		return (refus ? refus(reponse.status, code) : null) ?? { kind: 'panne', statut: reponse.status };
	}
	const corps = objet(await reponse.json().catch(() => null));
	const donnees = corps ? projeter(corps.donnees) : null;
	return donnees ? { kind: 'ok', donnees } : { kind: 'panne', statut: reponse.status };
}

/** Where to open the map of an address: its point, its pan bounds, its parcel, the served states. */
export function lireContexte(banId: string, options: OptionsLecture = {}): Promise<IssueCarte<ContexteCarte>> {
	return lire(CHEMIN_CONTEXTE.replace('{ban_id}', encodeURIComponent(banId)), projeterContexte, options);
}

/** Every parcel of one tile, whole. A parcel across two tiles is in both: fold them on `id`. */
export function lireTuileParcelles(z: number, x: number, y: number, options: OptionsLecture = {}): Promise<IssueCarte<TuileParcelles>> {
	const chemin = CHEMIN_TUILE_PARCELLES.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y));
	return lire(chemin, projeterTuile, options);
}

/**
 * One page of the sales of a parcel of the map, asked for the address of the fiche (the route answers
 * only around it). `vg` is the generation served with the former page; null on a first page.
 */
export function lireVentesParcelle(
	banId: string,
	idParcelle: string,
	page: { page: number; vg: string | null },
	options: OptionsLecture = {}
): Promise<IssueVentesCarte> {
	const sp = new URLSearchParams({ page: String(page.page) });
	if (page.vg !== null) sp.set('vg', page.vg);
	const chemin = CHEMIN_VENTES_PARCELLE.replace('{ban_id}', encodeURIComponent(banId)).replace('{id_parcelle}', encodeURIComponent(idParcelle));
	// A parcel refused around this address (404 without code) stays an outage of the sales, never a zero.
	return lire(`${chemin}?${sp.toString()}`, projeterVentes, options, (statut, code): IssueVentesCarte | null =>
		(statut === 404 && code === 'page_hors_limites') || (statut === 409 && code === 'generation_changee') ? { kind: 'page_invalide' } : null
	);
}
