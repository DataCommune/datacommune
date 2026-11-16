// What the departement page says (04.7-10, decision 6). Pure module, zero Svelte imports. Nothing is
// computed from the figures: the served model is read into four cards « value + definition +
// base », each following the state of its own block (a value, an absence with its state and its
// reason, never a zero); the same texts feed the visible page, the meta description and the
// structured data, so the markup never says what the page does not show.
//   - the definitions are those of the contract fields (`explications.ts`), not a text of this page;
//   - the units and the precision come from the registry (`formatters.ts`);
//   - the structured data is emitted for an INDEXED page that serves at least one figure.
import type { SourceAttribution } from '$lib/api/commune';
import type { DepartementRow, DepartementSynthese, FicheDepartementRow } from '$lib/api/communes';
import { libelleArrondissement } from '$lib/data/arrondissements';
import { populationServie } from '$lib/data/annuaire-communes';
import { dateEnLettres } from '$lib/data/commune-indicators';
import type { EntreeVolet, EtatKpi, SourceKpi, ValeurServie } from '$lib/data/commune-kpi';
import { LIBELLE_HORS_COUVERTURE, LIBELLE_NON_AFFICHABLE, LIBELLES_ETATS, LIBELLES_MOTIFS, libelleMotifAbsence } from '$lib/data/etats-libelles';
import { getExplication, type CoveredFieldId } from '$lib/data/explications';
import { LIBELLE_EN_CLAIR, enClairChamp } from '$lib/data/explications-simples';
import { displayUnit, formatBareNumber } from '$lib/data/formatters';
import { premiereQuiTient } from '$lib/seo/descriptions';

const NBSP = String.fromCharCode(0xa0);
const entier = new Intl.NumberFormat('fr-FR');

/** « 30 juin 2024 », « 1er juillet 2021 » from AAAA-MM-JJ, as the commune page writes a day; null when it is not one. */
function dateFr(iso: string | null | undefined): string | null {
	return iso ? dateEnLettres(iso) : null;
}

/** « 7 350 ventes », « 1 commune »: a count and its noun, a no-break space between them. */
function compte(n: number, singulier: string, pluriel: string = `${singulier}s`): string {
	return `${entier.format(n)}${NBSP}${n === 1 ? singulier : pluriel}`;
}

const NNBSP = String.fromCharCode(0x202f);

/**
 * The lead sentence under the title of the figures; the description of the Dataset starts with it.
 * « L’ensemble des communes » is true of the sales and of the diagnostics. The population adds up the
 * communes whose population is loaded: when they are fewer than the communes of the referential, the
 * sentence says how many (Opus P3-6), and keeps the scope of the prices and of the DPE apart.
 */
export function phrasePortee(synthese: Pick<DepartementSynthese, 'population' | 'communes'>): string {
	const comptees = synthese.population.communes_comptees;
	if (comptees !== null && comptees < synthese.communes.nombre) {
		return `La population additionne ${compte(comptees, 'commune')} sur ${synthese.communes.nombre}, celles dont la population est chargée${NNBSP}; les prix et la part de DPE portent sur l’ensemble des ventes retenues et des diagnostics du département${NBSP}: chaque vente et chaque diagnostic y est compté une fois.`;
	}
	return `Ces chiffres portent sur l’ensemble des communes du département${NBSP}: chaque vente et chaque diagnostic y est compté une fois.`;
}

/** Title of the section of the figures, also the name of its Dataset: both read this one text. */
export function titreChiffres(nom: string): string {
	return `Chiffres clés${NBSP}: ${nom}`;
}

export type EtatCarte = Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;

export interface CarteDepartement {
	id: 'population' | 'appartement' | 'maison' | 'dpe';
	libelle: string;
	/** Field of the contract that defines the figure (its definition is read in `explications.ts`). */
	fieldId: CoveredFieldId;
	definition: string;
	etat: EtatCarte;
	/** Raw served figure, when the card has one (structured data). */
	valeurBrute: number | null;
	/** What the figure rests on: its count, its window; null when there is nothing to rest on. */
	base: string | null;
	/** The base as the tile says it: the count alone, as on the commune fiche (D3, 2026-11-10). The exact base,
	 *  its period and its reserve are in the panel and on paper. */
	portee: string | null;
}

function valeur(fieldId: CoveredFieldId, brute: number): EtatCarte {
	return { kind: 'valeur', texte: formatBareNumber(fieldId, brute), unite: displayUnit(fieldId) };
}

/**
 * A declared absence, with the count the API served beside it (only a median under the threshold
 * keeps one). A block that is neither served nor declared is an API defect: it is shown as such,
 * with the neutral label and no sentence, never as a zero or as « Non renseigné ».
 */
function absence(declaree: { etat: string; motif: string } | null, effectif: number | null = null): EtatCarte {
	if (declaree === null) {
		return { kind: 'absence', etat: 'not_available', motif: '', effectif: null, libelle: LIBELLE_NON_AFFICHABLE, provisoire: true };
	}
	const etat = declaree.etat as Extract<EtatCarte, { kind: 'absence' }>['etat'];
	// The glossary sentence of a missing value speaks of « cette commune »: for these two states the
	// API writes the cause for the departement (no import yet, no sale, no known class), shown as is.
	const cause = etat === 'not_reported' || etat === 'zero_denominator' ? libelleMotifAbsence(declaree.motif) : null;
	return { kind: 'absence', etat, motif: declaree.motif, effectif, ...(cause ? { aide: cause } : {}) };
}

function basePrix(effectif: number | null, fenetre: DepartementSynthese['prix']['fenetre']): string | null {
	if (effectif === null) return null;
	const debut = dateFr(fenetre?.debut);
	const fin = dateFr(fenetre?.fin);
	const periode = debut && fin ? ` · du ${debut} au ${fin}` : '';
	return `${compte(effectif, 'vente')}${periode}`;
}

function porteePrix(effectif: number | null): string | null {
	return effectif === null ? null : compte(effectif, 'vente');
}

function cartePrix(
	id: 'appartement' | 'maison',
	libelle: string,
	fieldId: CoveredFieldId,
	prix: DepartementSynthese['prix']
): CarteDepartement {
	const bloc = prix[id];
	return {
		id,
		libelle,
		fieldId,
		definition: getExplication(fieldId).definition,
		etat: bloc.mediane_m2 !== null ? valeur(fieldId, bloc.mediane_m2) : absence(bloc.etat, bloc.effectif),
		valeurBrute: bloc.mediane_m2,
		base: basePrix(bloc.effectif, prix.fenetre),
		portee: porteePrix(bloc.effectif)
	};
}

function baseDpe(dpe: DepartementSynthese['dpe']): string | null {
	if (dpe.classes_connues === null || dpe.total === null) return null;
	if (dpe.classes_connues === 0) return `${compte(dpe.total, 'diagnostic')}, aucun de classe connue`;
	const debut = dateFr(dpe.date_min);
	const fin = dateFr(dpe.date_max);
	const periode = debut && fin ? ` · établis du ${debut} au ${fin}` : '';
	return `${compte(dpe.classes_connues, 'diagnostic')} de classe connue${periode}`;
}

function porteeDpe(dpe: DepartementSynthese['dpe']): string | null {
	if (dpe.classes_connues === null || dpe.total === null) return null;
	if (dpe.classes_connues === 0) return baseDpe(dpe);
	return `${compte(dpe.classes_connues, 'diagnostic')} de classe connue`;
}

/** The four cards, in the order of the decision: population, flats, houses, DPE classed F or G. */
export function cartesDepartement(synthese: DepartementSynthese): CarteDepartement[] {
	const { population, prix, dpe, communes } = synthese;
	const basePopulation =
		population.total !== null && population.communes_comptees !== null
			? `${compte(population.communes_comptees, 'commune')} · ${
					population.annee !== null ? `recensement ${population.annee}` : 'recensements de millésimes différents'
				}`
			: null;
	// A partial sum changes the meaning of the figure: the reserve stays on the tile (A9-7); the census year is in the panel.
	const partielle = population.communes_comptees !== null && population.communes_comptees < communes.nombre;
	const porteePopulation =
		basePopulation && population.communes_comptees !== null
			? `${compte(population.communes_comptees, 'commune')}${partielle ? ` sur ${communes.nombre}` : ''}`
			: null;
	return [
		{
			id: 'population',
			libelle: 'Population des communes',
			fieldId: 'departement.population.total',
			definition: getExplication('departement.population.total').definition,
			etat: population.total !== null ? valeur('departement.population.total', population.total) : absence(population.etat),
			valeurBrute: population.total,
			base: basePopulation,
			portee: porteePopulation
		},
		cartePrix('appartement', 'Prix médian au m² des appartements', 'departement.prix.appartement.mediane_m2', prix),
		cartePrix('maison', 'Prix médian au m² des maisons', 'departement.prix.maison.mediane_m2', prix),
		{
			id: 'dpe',
			libelle: 'Part des DPE classés F ou G',
			fieldId: 'departement.dpe.part_fg_pct',
			definition: getExplication('departement.dpe.part_fg_pct').definition,
			etat: dpe.part_fg_pct !== null ? valeur('departement.dpe.part_fg_pct', dpe.part_fg_pct) : absence(dpe.etat_part_fg),
			valeurBrute: dpe.part_fg_pct,
			base: baseDpe(dpe),
			portee: porteeDpe(dpe)
		}
	];
}

/** Order of « L'essentiel » (D2, 2026-11-10): the prices first, as the commune fiche, the population last. The
 *  structured data and the meta description keep the order of `cartesDepartement`. */
const ORDRE_ESSENTIEL: readonly CarteDepartement['id'][] = ['maison', 'appartement', 'dpe', 'population'];

export function cartesEssentiel(synthese: DepartementSynthese): CarteDepartement[] {
	const cartes = cartesDepartement(synthese);
	return ORDRE_ESSENTIEL.map((id) => cartes.find((carte) => carte.id === id)!);
}

export interface VoletCarte {
	entrees: EntreeVolet[];
	brut: ValeurServie[];
	source: SourceKpi | null;
}

// The API names its sources, it does not key them on the blocks of the answer.
const SOURCE_DE_CARTE: Record<CarteDepartement['id'], RegExp> = {
	population: /recensement/i,
	appartement: /dvf/i,
	maison: /dvf/i,
	dpe: /dpe/i
};

/** What the panel of a tile says (level 2, 4.9): nothing here is new, it left the tile for the panel. */
export function voletCarte(
	carte: CarteDepartement,
	synthese: Pick<DepartementSynthese, 'population' | 'communes'>,
	sources: readonly SourceAttribution[]
): VoletCarte {
	const servie = sources.find((source) => source.nom && SOURCE_DE_CARTE[carte.id].test(source.nom));
	const entrees: EntreeVolet[] = [
		{ terme: LIBELLE_EN_CLAIR, valeur: enClairChamp(carte.fieldId) ?? '' },
		{ terme: 'Définition', valeur: carte.definition },
		{ terme: 'Base', valeur: carte.base ?? '' },
		{ terme: 'Périmètre', valeur: phrasePortee(synthese) },
		{ terme: 'Source', valeur: servie?.nom ?? '' },
		{ terme: 'Licence', valeur: servie?.licence ?? '' }
	];
	if (carte.etat.kind === 'absence') entrees.push({ terme: 'État et motif', absences: [{ libelle: null, etat: carte.etat }] });
	const { etat } = carte;
	return {
		entrees,
		brut: [
			{
				fieldId: carte.fieldId,
				valeur: JSON.stringify(carte.valeurBrute),
				etat: etat.kind === 'absence' ? { kind: 'absence', etat: etat.etat, motif: etat.motif } : { kind: 'valeur' }
			}
		],
		source: servie?.nom ? { nom: servie.nom, url: servie.url ?? null } : null
	};
}

/** At least one figure of the page is served. */
export function aDesChiffres(synthese: DepartementSynthese): boolean {
	return cartesDepartement(synthese).some((carte) => carte.etat.kind === 'valeur');
}

/** « 3 480 €/m² » as the card shows it. */
function valeurAvecUnite(etat: EtatCarte): string | null {
	if (etat.kind !== 'valeur') return null;
	return etat.unite ? `${etat.texte}${NBSP}${etat.unite}` : etat.texte;
}

/** The sentence a served card says: « Libellé : valeur (base). » */
function texteCarte(carte: CarteDepartement): string | null {
	const chiffre = valeurAvecUnite(carte.etat);
	if (chiffre === null) return null;
	return `${carte.libelle}${NBSP}: ${chiffre}${carte.base ? ` (${carte.base})` : ''}.`;
}

/** Meta description: the figures the page serves, nothing else. */
export function descriptionMeta(synthese: DepartementSynthese): string {
	const [population, appartement, maison, dpe] = cartesDepartement(synthese);
	const parties: string[] = [];
	const chiffre = (carte: CarteDepartement) => valeurAvecUnite(carte.etat);
	if (chiffre(population)) parties.push(chiffre(population)!);
	if (chiffre(appartement)) parties.push(`appartements à ${chiffre(appartement)}`);
	if (chiffre(maison)) parties.push(`maisons à ${chiffre(maison)}`);
	if (chiffre(dpe)) parties.push(`${chiffre(dpe)} des DPE classés F ou G`);
	const nom = `${synthese.nom}${NBSP}(${synthese.code})`;
	if (parties.length === 0) {
		return `Chiffres clés du département ${nom} et ses communes les plus peuplées, avec leurs sources.`;
	}
	// The closing sentence gives way before a figure does: the snippet is cut past 155 characters.
	const chiffres = `${nom}${NBSP}: ${parties.join(', ')}.`;
	return premiereQuiTient([`${chiffres} Chiffres clés du département, avec leurs sources.`, `${chiffres} Avec leurs sources.`, chiffres]);
}

/**
 * Description of the Dataset, written from the visible texts: the lead sentence of the section and
 * the sentence of each served card. Null when no figure is served (no Dataset then), and always
 * within the 50 to 5 000 characters that the search engines ask for.
 */
export function descriptionDataset(synthese: DepartementSynthese): string | null {
	const phrases = cartesDepartement(synthese).map(texteCarte).filter((p): p is string => p !== null);
	if (phrases.length === 0) return null;
	const description = [phrasePortee(synthese), ...phrases].join(' ');
	return description.length >= 50 && description.length <= 5000 ? description : null;
}

/** The day of the last import of the data of the page, or that none was made; nothing out of coverage. */
export function phraseImportation(synthese: Pick<DepartementSynthese, 'derniere_importation' | 'couverture'>): string | null {
	if (synthese.derniere_importation !== null) {
		const jour = dateFr(synthese.derniere_importation);
		return jour ? `Données importées le ${jour}.` : null;
	}
	const hebergee = Object.values(synthese.couverture).some((hebergement) => hebergement.etat === 'heberge');
	return hebergee ? 'Aucune donnée n’a encore été importée pour ce département.' : null;
}

export interface EtapeFil {
	nom: string;
	href: string;
}

/**
 * The departement as a step of the breadcrumb of a commune page. Paris is a departement and a commune:
 * « Paris / Paris » would be two links of the same name leading to two pages (WCAG 2.4.4), so the step of
 * the departement says what it is, « Paris (département) », when a step that follows bears its name.
 */
export function nomDepartementDuFil(nom: string, suivants: readonly string[]): string {
	const normalise = (texte: string) => texte.trim().toLocaleLowerCase('fr');
	return suivants.some((suivant) => normalise(suivant) === normalise(nom)) ? `${nom} (département)` : nom;
}

/** The two first steps of every breadcrumb of the departement pages; the root is « Accueil », as on every
 *  page of the site (T6, decided by Ianis on 2026-11-10). */
const FIL_DEPARTEMENTS: readonly EtapeFil[] = [
	{ nom: 'Accueil', href: '/' },
	{ nom: 'Départements', href: '/departements' }
];

/** Breadcrumb of the list of departements: the page is its last step. */
export function filArianeListe(): EtapeFil[] {
	return [...FIL_DEPARTEMENTS];
}

/** Breadcrumb of a departement page: one list, read by the visible nav and by the BreadcrumbList. */
export function filAriane(code: string, nom: string): EtapeFil[] {
	return [...FIL_DEPARTEMENTS, { nom, href: `/departement/${code}` }];
}

function listeFil(etapes: readonly EtapeFil[], siteUrl: string): Record<string, unknown> {
	return {
		'@context': 'https://schema.org',
		'@type': 'BreadcrumbList',
		itemListElement: etapes.map((etape, index) => ({
			'@type': 'ListItem',
			position: index + 1,
			name: etape.nom,
			item: `${siteUrl}${etape.href}`
		}))
	};
}

/**
 * The directory filtered on the departement. When nothing of it is hosted the directory would
 * answer « aucune commune » (it lists the served departements by default): the link then asks for
 * the whole country, where its communes are listed without prices.
 */
export function lienToutesLesCommunes(synthese: Pick<DepartementSynthese, 'code' | 'couverture'>): string {
	const hebergements = Object.values(synthese.couverture);
	const rienHeberge = hebergements.length > 0 && hebergements.every((h) => h.etat === 'hors_couverture');
	return `/communes?departement=${synthese.code}${rienHeberge ? '&couverture=toutes' : ''}`;
}

export interface LigneCommunePrincipale {
	code: string;
	href: string;
	/** What the reader sees: the commune, or « 12e arrondissement de Paris ». */
	nom: string;
	/** Population as the directory writes it; null when none is served (never a zero). */
	population: ReturnType<typeof populationServie>;
}

export function lignesCommunesPrincipales(synthese: DepartementSynthese): LigneCommunePrincipale[] {
	return synthese.communes_principales.map((zone) => ({
		code: zone.code_insee,
		href: `/commune/${encodeURIComponent(zone.code_insee)}`,
		nom: (zone.type_zone === 'arrondissement' ? libelleArrondissement(zone.code_insee) : null) ?? zone.nom,
		population: populationServie(zone)
	}));
}

/** The principal zones are the arrondissements when the departement has one commune only (Paris). */
export function titreCommunesPrincipales(synthese: Pick<DepartementSynthese, 'communes_principales'>): string {
	return synthese.communes_principales.some((zone) => zone.type_zone === 'arrondissement')
		? 'Arrondissements les plus peuplés'
		: 'Communes les plus peuplées';
}

/**
 * Address of the text of a licence, by the label the API serves (`meta.source[].licence`), for the
 * `license` of the structured data: schema.org wants an address there, and the answer of the
 * departement carries the label only. The addresses are those of the source catalogue of the API
 * (`source_catalog.py`, checked by the test); a label that is not here gets no address, never a guess.
 */
export const URL_LICENCES: Readonly<Record<string, string>> = {
	'Licence Ouverte 2.0': 'https://www.data.gouv.fr/pages/legal/licences/etalab-2.0',
	'Licence Ouverte 1.0': 'https://www.data.gouv.fr/pages/legal/licences/etalab-1.0',
	'ODbL 1.0': 'https://opendatacommons.org/licenses/odbl/1-0/'
};

const urlLicence = (libelle: string | undefined): string | null =>
	libelle && Object.hasOwn(URL_LICENCES, libelle) ? URL_LICENCES[libelle] : null;

interface DonneesStructureesEntree {
	synthese: DepartementSynthese;
	sources: readonly SourceAttribution[];
	siteUrl: string;
}

/**
 * The structured data of the page: a BreadcrumbList and the Dataset of the figures, for an indexed
 * page that serves at least one figure, else nothing (a `noindex` page, or a page of absences, claims
 * no dataset). The Dataset is named as the visible title of the section and described by the
 * visible texts; its time coverage is the DVF window alone, named in the prices it qualifies.
 */
export function donneesStructurees({ synthese, sources, siteUrl }: DonneesStructureesEntree): Record<string, unknown>[] {
	const description = descriptionDataset(synthese);
	if (!synthese.indexable || description === null) return [];

	const url = `${siteUrl}/departement/${synthese.code}`;
	const cartes = cartesDepartement(synthese);
	const fenetre = synthese.prix.fenetre;
	const fenetreServie = Boolean(fenetre) && cartes.some((c) => (c.id === 'appartement' || c.id === 'maison') && c.etat.kind === 'valeur');

	const mesures = cartes
		.filter((carte) => carte.etat.kind === 'valeur' && carte.valeurBrute !== null)
		.map((carte) => ({
			'@type': 'PropertyValue',
			name: carte.libelle,
			value: carte.valeurBrute,
			...(carte.etat.kind === 'valeur' && carte.etat.unite ? { unitText: carte.etat.unite } : {}),
			...(carte.base ? { description: carte.base } : {})
		}));

	const licences = [...new Set(sources.map((s) => urlLicence(s.licence)).filter((l): l is string => l !== null))];
	const sourcesNommees = sources.filter((s) => Boolean(s.nom));

	return [
		listeFil(filAriane(synthese.code, synthese.nom), siteUrl),
		{
			'@context': 'https://schema.org',
			'@type': 'Dataset',
			name: titreChiffres(synthese.nom),
			description,
			url,
			inLanguage: 'fr',
			isAccessibleForFree: true,
			creator: { '@type': 'Organization', name: 'DataCommune', url: siteUrl },
			spatialCoverage: { '@type': 'AdministrativeArea', name: synthese.nom, identifier: synthese.code, url },
			...(fenetreServie && fenetre ? { temporalCoverage: `${fenetre.debut}/${fenetre.fin}` } : {}),
			variableMeasured: mesures,
			...(licences.length ? { license: licences.length === 1 ? licences[0] : licences } : {}),
			...(sourcesNommees.length
				? {
						// CreativeWork, not Dataset: a Dataset there would ask for a description of its own.
						isBasedOn: sourcesNommees.map((source) => ({
							'@type': 'CreativeWork',
							name: source.nom,
							...(ligneSource(source)?.href ? { url: ligneSource(source)?.href } : {}),
							...(urlLicence(source.licence) ? { license: urlLicence(source.licence) } : {}),
							...(source.producteur ? { creator: { '@type': 'Organization', name: source.producteur } } : {})
						}))
					}
				: {})
		}
	];
}

/** « 207 communes », « 1 commune et 20 arrondissements »: the size of the departement, never a sum of both. */
export function phraseTaille(communes: DepartementSynthese['communes']): string {
	const base = compte(communes.nombre, 'commune');
	return communes.arrondissements > 0 ? `${base} et ${compte(communes.arrondissements, 'arrondissement')}` : base;
}

/** Why the list of principal communes is empty: the coverage reason when nothing is hosted, else the referential. */
export function phraseListeVide(synthese: Pick<DepartementSynthese, 'couverture'>): string {
	const hebergements = Object.values(synthese.couverture);
	const rienHeberge = hebergements.length > 0 && hebergements.every((h) => h.etat === 'hors_couverture');
	const motif = hebergements[0]?.motif;
	if (rienHeberge && motif && Object.hasOwn(LIBELLES_MOTIFS, motif)) return LIBELLES_MOTIFS[motif].phrase;
	return 'Le référentiel ne liste aucune commune pour ce département.';
}

export interface LigneSource {
	nom: string;
	/** « · Producteur · Licence », in its own element after the name: the spacing is the layout's, not a space to keep. */
	detail: string;
	/** Address of the source, only when it is a web address. */
	href: string | null;
}

/** A source of the attribution as the page lists it; null without a name. */
export function ligneSource(source: SourceAttribution): LigneSource | null {
	if (!source.nom) return null;
	const reste = [source.producteur, source.licence].filter(Boolean);
	return {
		nom: source.nom,
		detail: reste.length ? `· ${reste.join(' · ')}` : '',
		href: source.url && /^https:\/\//.test(source.url) ? source.url : null
	};
}

/** What a row of the list of departements says when its page serves no prices; null when it does. */
export function etatLigneDepartement(ligne: Pick<DepartementRow, 'prix' | 'indexable'>): string | null {
	if (ligne.indexable) return null;
	if (ligne.prix.etat === 'hors_couverture') {
		const motif = ligne.prix.motif;
		return motif && Object.hasOwn(LIBELLES_MOTIFS, motif) ? LIBELLES_MOTIFS[motif].libelle : LIBELLE_HORS_COUVERTURE;
	}
	// Hosted, not imported yet: the common label of the state, as the tiles of its page say it (D13, 2026-11-10).
	return LIBELLES_ETATS.not_reported;
}

/** Structured data of `/departements`: its breadcrumb. */
export function donneesStructureesListe(siteUrl: string): Record<string, unknown>[] {
	return [listeFil(filArianeListe(), siteUrl)];
}

/** A letter of the A-Z directory of a departement and the pages under it. */
export interface LettreAnnuaire {
	lettre: string;
	fiches: { code: string; href: string; nom: string }[];
}

/** The first letter of a name, without its accent (« Île-d’Yeu » under I); « # » for anything else. */
function lettreDe(nom: string): string {
	// « Œting » (57) under O: the ligature has no decomposition.
	const lettre = nom.normalize('NFD').replace(/\p{M}/gu, '').replace(/^[Œœ]/, 'O').charAt(0).toUpperCase();
	return /^[A-Z]$/.test(lettre) ? lettre : '#';
}

/**
 * Every page of the departement in alphabetical order, grouped by first letter (SEO audit
 * 2026-11-14: each commune page linked from its departement). Sorted on the served name, numbers
 * read as numbers, so that an arrondissement follows its commune in its order (« Marseille »,
 * then its 1st to 16th); an arrondissement shows its readable title.
 */
export function annuaireParLettre(fiches: readonly FicheDepartementRow[]): LettreAnnuaire[] {
	const triees = [...fiches].sort(
		(a, b) => a.nom.localeCompare(b.nom, 'fr', { numeric: true, sensitivity: 'base' }) || a.code_insee.localeCompare(b.code_insee)
	);
	const groupes: LettreAnnuaire[] = [];
	for (const fiche of triees) {
		const lettre = lettreDe(fiche.nom);
		if (groupes.at(-1)?.lettre !== lettre) groupes.push({ lettre, fiches: [] });
		groupes.at(-1)!.fiches.push({
			code: fiche.code_insee,
			href: `/commune/${fiche.code_insee}`,
			nom: (fiche.type_zone === 'arrondissement' ? libelleArrondissement(fiche.code_insee) : null) ?? fiche.nom
		});
	}
	return groupes;
}
