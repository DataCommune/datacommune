// Address report (`GET /v1/adresse/{ban_id}/rapport`, 04.7-01): types and projection of `donnees`.
// Pure module, zero Svelte imports. The projection keeps every served theme, known or not, so that
// the page renders the themes plans 04 to 08 add without a front change; an unknown enum value is
// brought back to its most cautious reading (a state without answer, a neutral tone), never dropped
// and never turned into a value.

export type SectionRapport = 'batiment' | 'risques' | 'marche' | 'vivre';
/** `en_cours` (R-07): served to a short wait only, the source of the theme has not answered yet. */
export type EtatTheme = 'ok' | 'zero' | 'manque' | 'hors_couverture' | 'panne' | 'non_concerne' | 'en_cours';
export type TonTheme = 'vigilance' | 'rassurant' | 'neutre' | 'info';
export type ConfianceRattachement = 'sure' | 'probable' | 'incertaine';

/** An arrondissement of Paris, Lyon or Marseille, as served (`ArrondissementRef`). */
export interface ArrondissementRapport {
	code: string;
	nom: string;
}

export interface GranulariteRapport {
	/** `adresse`, `batiment`, `parcelle`, `rayon`, `iris`, `arrondissement`, `commune`, `epci`, `departement`, or a later one. */
	niveau: string;
	rayon_m: number | null;
	/** Served with the `arrondissement` level only; absent when not served whole, never guessed (D-33). */
	arrondissement?: ArrondissementRapport | null;
}

export interface ChiffreRapport {
	id: string;
	libelle: string;
	valeur: number | string;
	unite: string;
	millesime: string | null;
}

export interface MaillonRapport {
	lien: string;
	confiance: ConfianceRattachement;
}

export interface RattachementRapport {
	entite: string;
	cle: string | null;
	/** Served confidence: the weakest of its links (UI-SPEC § 5, rule 4), as the API computes it. */
	confiance: ConfianceRattachement;
	candidats: number | null;
	motif: string | null;
	/** Served alert line of a link that is not sure (04.7-17). */
	alerte: string | null;
	/** Served links; null when the served chain is malformed (kept as such, never repaired). */
	maillons: MaillonRapport[] | null;
}

export interface SourceRapport {
	cle: string;
	nom: string;
	producteur: string;
	url: string;
	licence: string;
	millesime: string | null;
	mode: 'direct' | 'import';
}

export interface ThemeRapport {
	id: string;
	section: SectionRapport;
	titre: string;
	etat: EtatTheme;
	ton: TonTheme;
	granularite: GranulariteRapport;
	verdict: string;
	chiffres: ChiffreRapport[];
	/** Theme-specific structure, rendered generically when the front does not know it. */
	details: Record<string, unknown>;
	rattachement: RattachementRapport | null;
	sources: SourceRapport[];
	motif: string | null;
	/** Served wording of the reason of a gap or of an out of coverage (04.7-17). */
	etat_libelle: string | null;
	/** Served headline (40 characters at most), null when the API serves none. */
	accroche: string | null;
	pannes_partielles: string[];
}

export type IdChiffreCle = 'prix_m2_median' | 'loyer_m2' | 'dpe_dominant' | 'annee_construction';

export interface ChiffreCleRapport {
	id: IdChiffreCle | string;
	libelle: string;
	theme: string;
	etat: EtatTheme;
	granularite: GranulariteRapport | null;
	chiffres: ChiffreRapport[];
	motif: string | null;
}

export interface ParSectionRapport {
	vigilance: number;
	pannes: number;
	lus: number;
	total: number;
}

/**
 * Served summary (04.7-17): the theme ids of each list, split by scale by the API (a radius counts
 * for the address up to 1 km), and its counts. The front never splits nor counts again.
 */
export interface ResumeServi {
	pointsAdresse: string[];
	pointsSecteur: string[];
	favorablesAdresse: string[];
	favorablesSecteur: string[];
	pannes: string[];
	/** Keys of the sources that did not answer. */
	sourcesEnPanne: string[];
	partiels: string[];
	sansObjet: string[];
	sansValeur: string[];
	/** Themes whose read has not ended (R-07); absent from a summary served before it. */
	enCours?: string[];
	lus: number;
	lusAdresse: number;
	lusSecteur: number;
	total: number;
	parSection: Record<SectionRapport, ParSectionRapport>;
}

/** Static map of the head (04.7-17): two images on the site's own origin, under `/carte/`. */
export interface CarteRapport {
	plan: string;
	parcelles: string;
	empriseM: number;
	attribution: string;
	licence: string;
	/** Link to the Géoportail, kept only on its own https origin. */
	geoportail: string | null;
}

export interface RapportPublic {
	/** Day the report is computed for (Paris), ISO date. */
	date: string | null;
	themes: ThemeRapport[];
	chiffresCles: ChiffreCleRapport[];
	/** Served attachment sentence of the head, null when the API says nothing (or serves nothing). */
	rattachementPhrase: string | null;
	/** Served summary; null when it is not served in full. */
	resume: ResumeServi | null;
	carte: CarteRapport | null;
}

type Brut = Record<string, unknown> | null | undefined;

const SECTIONS: readonly SectionRapport[] = ['batiment', 'risques', 'marche', 'vivre'];
const ETATS: readonly EtatTheme[] = ['ok', 'zero', 'manque', 'hors_couverture', 'panne', 'non_concerne', 'en_cours'];
const TONS: readonly TonTheme[] = ['vigilance', 'rassurant', 'neutre', 'info'];
const CONFIANCES: readonly ConfianceRattachement[] = ['sure', 'probable', 'incertaine'];
export const ETATS_SANS_REPONSE: ReadonlySet<EtatTheme> = new Set(['manque', 'hors_couverture', 'panne', 'en_cours']);

const texte = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null);
const nombre = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const objet = (v: unknown): Record<string, unknown> =>
	v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const liste = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const parmi = <T extends string>(v: unknown, valeurs: readonly T[]): T | null =>
	typeof v === 'string' && (valeurs as readonly string[]).includes(v) ? (v as T) : null;

function projeterGranularite(brut: unknown): GranulariteRapport | null {
	const g = objet(brut);
	const niveau = texte(g.niveau);
	if (!niveau) return null;
	// The long form of the scale names the arrondissement from the served reference only (D-33).
	const a = objet(g.arrondissement);
	const [code, nom] = [texte(a.code), texte(a.nom)];
	return code && nom ? { niveau, rayon_m: nombre(g.rayon_m), arrondissement: { code, nom } } : { niveau, rayon_m: nombre(g.rayon_m) };
}

function projeterChiffre(brut: unknown): ChiffreRapport | null {
	const c = objet(brut);
	const valeur = typeof c.valeur === 'string' || nombre(c.valeur) !== null ? (c.valeur as number | string) : null;
	const id = texte(c.id);
	if (valeur === null || id === null) return null;
	return { id, libelle: texte(c.libelle) ?? id, valeur, unite: texte(c.unite) ?? '', millesime: texte(c.millesime) };
}

function projeterSource(brut: unknown): SourceRapport | null {
	const s = objet(brut);
	const nom = texte(s.nom);
	if (!nom) return null;
	return {
		cle: texte(s.cle) ?? nom,
		nom,
		producteur: texte(s.producteur) ?? '',
		url: texte(s.url) ?? '',
		licence: texte(s.licence) ?? '',
		millesime: texte(s.millesime),
		mode: s.mode === 'direct' ? 'direct' : 'import'
	};
}

function projeterMaillon(brut: unknown): MaillonRapport | null {
	const m = objet(brut);
	const lien = texte(m.lien);
	// An unknown confidence is read as the weakest one: it never reassures.
	return lien ? { lien, confiance: parmi(m.confiance, CONFIANCES) ?? 'incertaine' } : null;
}

function projeterRattachement(brut: unknown): RattachementRapport | null {
	const r = objet(brut);
	const entite = texte(r.entite);
	if (!entite) return null;
	// Absent (a report served before 04.7-17): no link; a chain with one bad link is not a chain.
	const lus = r.maillons === undefined ? [] : Array.isArray(r.maillons) ? r.maillons.map(projeterMaillon) : [null];
	const maillons = lus.every((m): m is MaillonRapport => m !== null) ? lus : null;
	return {
		entite,
		cle: texte(r.cle),
		// An unknown confidence is read as the weakest one: it never reassures.
		confiance: parmi(r.confiance, CONFIANCES) ?? 'incertaine',
		candidats: nombre(r.candidats),
		motif: texte(r.motif),
		alerte: texte(r.alerte),
		maillons
	};
}


/** One theme; null only when it lacks what any row needs (an id, a title, a verdict). */
export function projeterTheme(brut: unknown): ThemeRapport | null {
	const t = objet(brut);
	const id = texte(t.id);
	const titre = texte(t.titre);
	const verdict = texte(t.verdict);
	if (!id || !titre || !verdict) return null;
	// An unknown state is a gap: never an answer, never a zero.
	const etat = parmi(t.etat, ETATS) ?? 'manque';
	const sansReponse = ETATS_SANS_REPONSE.has(etat);
	const tonServi = parmi(t.ton, TONS);
	// The API serves the tone the page shows (04.7-17): never favourable on a link not established.
	const ton: TonTheme = sansReponse ? 'info' : tonServi && tonServi !== 'info' ? tonServi : 'neutre';
	const accroche = texte(t.accroche);
	return {
		id,
		section: parmi(t.section, SECTIONS) ?? 'vivre',
		titre,
		etat,
		ton,
		granularite: projeterGranularite(t.granularite) ?? { niveau: 'commune', rayon_m: null },
		verdict,
		// A state without answer carries no figure (04.7-01 model); a served one is kept as is.
		chiffres: sansReponse ? [] : liste(t.chiffres).map(projeterChiffre).filter((c): c is ChiffreRapport => c !== null).slice(0, 3),
		details: objet(t.details),
		rattachement: projeterRattachement(t.rattachement),
		sources: liste(t.sources).map(projeterSource).filter((s): s is SourceRapport => s !== null),
		motif: texte(t.motif),
		etat_libelle: texte(t.etat_libelle),
		// The contract caps it at 40 characters: a longer one is not a headline, it is not shown.
		accroche: accroche !== null && [...accroche].length <= 40 ? accroche : null,
		pannes_partielles: liste(t.pannes_partielles).filter((p): p is string => typeof p === 'string')
	};
}

function projeterChiffreCle(brut: unknown): ChiffreCleRapport | null {
	const k = objet(brut);
	const id = texte(k.id);
	const theme = texte(k.theme);
	if (!id || !theme) return null;
	return {
		id,
		libelle: texte(k.libelle) ?? id,
		theme,
		etat: parmi(k.etat, ETATS) ?? 'manque',
		granularite: projeterGranularite(k.granularite),
		chiffres: liste(k.chiffres).map(projeterChiffre).filter((c): c is ChiffreRapport => c !== null),
		motif: texte(k.motif)
	};
}

const entier = (v: unknown): number | null => (Number.isInteger(v) && (v as number) >= 0 ? (v as number) : null);

/** Every element a text, or null: a list with one bad element is not served, never shortened. */
function textes(v: unknown): string[] | null {
	if (!Array.isArray(v)) return null;
	const t = v.map(texte);
	return t.every((x): x is string => x !== null) ? t : null;
}

/** Theme ids of a served list of points; null when it is not a list of points. */
const idsDePoints = (v: unknown): string[] | null => (Array.isArray(v) ? textes(v.map((p) => objet(p).theme)) : null);

function projeterParSection(brut: unknown): ParSectionRapport | null {
	const p = objet(brut);
	const [vigilance, pannes, lus, total] = [p.vigilance, p.pannes, p.lus, p.total].map(entier);
	return vigilance !== null && pannes !== null && lus !== null && total !== null ? { vigilance, pannes, lus, total } : null;
}

/** The served summary, or null when a part of it is not served: the page never fills it itself. */
function projeterResume(resume: Record<string, unknown>): ResumeServi | null {
	const listes = {
		pointsAdresse: idsDePoints(resume.points_adresse),
		pointsSecteur: idsDePoints(resume.points_commune),
		favorablesAdresse: idsDePoints(resume.rassurants_adresse),
		favorablesSecteur: idsDePoints(resume.rassurants_commune),
		pannes: idsDePoints(resume.pannes),
		partiels: idsDePoints(resume.partiels),
		sansObjet: idsDePoints(resume.non_concernes),
		sansValeur: idsDePoints(resume.sans_valeur)
	};
	// R-07: served since the short wait exists; a summary without it has no read in progress.
	const enCours = resume.en_cours === undefined ? undefined : idsDePoints(resume.en_cours);
	const sourcesEnPanne = textes(resume.sources_en_panne);
	const [lus, lusAdresse, lusSecteur, total] = [resume.lus, resume.lus_adresse, resume.lus_secteur, resume.total_criteres].map(entier);
	const sections = objet(resume.par_section);
	const parSection = Object.fromEntries(SECTIONS.map((s) => [s, projeterParSection(sections[s])]));
	if (Object.values(listes).some((l) => l === null) || sourcesEnPanne === null || enCours === null) return null;
	if (lus === null || lusAdresse === null || lusSecteur === null || total === null) return null;
	if (Object.values(parSection).some((p) => p === null)) return null;
	return {
		...(listes as Record<keyof typeof listes, string[]>),
		...(enCours ? { enCours } : {}),
		sourcesEnPanne,
		lus,
		lusAdresse,
		lusSecteur,
		total,
		parSection: parSection as Record<SectionRapport, ParSectionRapport>
	};
}

// A path of the site's own map proxy (`/carte/<BAN key>/<layer>.png`): never another origin (the CSP
// keeps `img-src 'self'`), never a protocol-relative URL, never a dot or an escape that would resolve
// elsewhere; each image only on its own layer.
const cheminCarte = (v: unknown, couche: 'plan' | 'parcelles'): string | null => {
	const c = texte(v);
	return c !== null && new RegExp(`^/carte/[A-Za-z0-9_-]+/${couche}\\.png$`).test(c) ? c : null;
};

function lienGeoportail(v: unknown): string | null {
	const t = texte(v);
	if (!t) return null;
	try {
		const u = new URL(t);
		return u.protocol === 'https:' && u.host === 'www.geoportail.gouv.fr' ? u.href : null;
	} catch {
		return null;
	}
}

/** The served map, or null: both images under `/carte/`, a positive extent, its credit. */
function projeterCarte(brut: unknown): CarteRapport | null {
	const c = objet(brut);
	const images = objet(c.images);
	const plan = cheminCarte(images.plan, 'plan');
	const parcelles = cheminCarte(images.parcelles, 'parcelles');
	const empriseM = nombre(c.emprise_m);
	const attribution = texte(c.attribution);
	const licence = texte(c.licence);
	if (!plan || !parcelles || empriseM === null || empriseM <= 0 || !attribution || !licence) return null;
	return { plan, parcelles, empriseM, attribution, licence, geoportail: lienGeoportail(c.geoportail) };
}

/** Ids of the themes still being read (R-07): the rows the follow-up of the page completes. */
export const lecturesEnCours = (rapport: RapportPublic): string[] => rapport.themes.filter((t) => t.etat === 'en_cours').map((t) => t.id);

/** `donnees` of the report, minus the address identity (projected by the address client). */
export function projeterRapport(donnees: Brut): RapportPublic {
	const d = objet(donnees);
	const resume = objet(d.resume);
	return {
		date: texte(d.date),
		themes: liste(d.themes).map(projeterTheme).filter((t): t is ThemeRapport => t !== null),
		chiffresCles: liste(resume.chiffres_cles).map(projeterChiffreCle).filter((k): k is ChiffreCleRapport => k !== null),
		rattachementPhrase: texte(d.rattachement_phrase),
		resume: projeterResume(resume),
		carte: projeterCarte(d.carte)
	};
}
