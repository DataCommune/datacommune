// Sales sections of the address page (UI-SPEC « /adresse/[id] » point 5, « Contrat de rédaction »;
// D-114, D-115, LIEU-02, SEM-01, SEM-02). Everything here words what the API serves: the order,
// the count, the withheld prices, the distances and the applied criteria are the API's. Nothing is
// filtered, sorted or counted in the browser (D-51); no sale is attached to a dwelling.
import type {
	CarteVente,
	CriteresAppliques,
	CriteresRelayes,
	DocumentsDpeAdresse,
	VentesAdresse,
	VentesParcelle,
	VentesRessemblantes
} from '$lib/api/adresses';
import type { EntreeVolet } from './commune-kpi';
import { NON_RENSEIGNEE, libelleRattachement, moisAnnee, qualificatifSelection } from './adresse-fiche';
import { LISTE_INCOMPLETE } from './adresse-lieu';
import { LIBELLE_NON_AFFICHABLE } from './etats-libelles';
import { definitionPublique } from './explications';
import { LIBELLE_EN_CLAIR, RAISON_COMPARABLES_INCOMPLETS, enClairChamp } from './explications-simples';
import { displayUnit, formatBareNumber } from './formatters';
import { parametresDePageValides } from '$lib/api/adresses';

const NB = ' ';
const NNB = ' ';

export const TITRE_VENTES = 'Ventes publiées sur la parcelle';
export const MESSAGE_PANNE_VENTES = "Les ventes n'ont pas pu être lues. Le reste de la page reste affiché.";
export const RECHARGER_VENTES = 'Recharger les ventes';
export const BADGE_PRIX_NON_AFFICHE = 'Prix non affiché';
/**
 * C4 (plan 04.5-07): the neutral badge of a VEFA card, on the template of « Prix non affiché »; text of
 * the report 4.1-15 applied as written, to be read in a block by Ianis (R10).
 */
export const BADGE_VEFA = 'VEFA, hors statistiques';
/** 04-DECISIONS-TEXTES row 32: no price band is served; the price of a recent isolated sale is withheld. */
export const PHRASE_VENTE_ISOLEE = `Vente isolée récente${NB}: le prix n'est pas publié, pour ne pas désigner un bien et son acheteur.`;
/** Row 36: a recent sale whose isolation cannot be established keeps its price withheld. */
export const PHRASE_ISOLEMENT_INDETERMINE = `Prix non publié${NB}: on ne peut pas établir que la vente n'est pas isolée.`;
/** Row 35: one business local, and a mutation without any described local. */
export const AVEC_UN_LOCAL_ACTIVITE = "avec un local d'activité";
/** Several business locals (04-DECISIONS-TEXTES row 57). */
export const AVEC_DES_LOCAUX_ACTIVITE = "avec des locaux d'activité";
export const SANS_LOCAL_DECRIT = 'sans local décrit';

const FIELD_PRIX = 'adresse.dvf.vente.prix';
const FIELD_PRIX_M2 = 'adresse.dvf.vente.prix_m2';
const FIELD_SURFACE = 'adresse.dvf.vente.surface_batie';
const FIELD_NOMBRE = 'adresse.dvf.ventes_parcelle.nombre';

/** A served zero carries its source and its period (UI-SPEC « Les quatre états »). */
export function phraseZeroVentes(periode: string | null): string {
	return periode ? `Aucune vente publiée sur la parcelle dans DVF (${periode}).` : 'Aucune vente publiée sur la parcelle dans DVF.';
}

/** Line 1 of a card: « {mois année} · {nature} », as served. */
export function ligneDateNature(carte: CarteVente): string {
	return [moisAnnee(carte.date), carte.nature].filter(Boolean).join(' · ');
}

const PLURIELS: ReadonlyArray<[keyof CarteVente['composition'], string, string]> = [
	['maisons', 'maison', 'maisons'],
	['appartements', 'appartement', 'appartements'],
	['dependances', 'dépendance', 'dépendances']
];

/** One part of a served composition; `provisoire` when the UI-SPEC gives it no wording. */
export interface PartieComposition {
	texte: string;
	provisoire: boolean;
}

/**
 * « 1 maison » / « 2 appartements, 1 dépendance » from the served counts; « avec un local d'activité »
 * for one business local (row 35), « avec des locaux d'activité » for several (row 57) and « sans local
 * décrit » for a mutation without any local (row 35).
 */
export function partiesComposition(composition: CarteVente['composition']): PartieComposition[] {
	const parties: PartieComposition[] = PLURIELS.flatMap(([cle, un, plusieurs]) => {
		const n = composition[cle];
		return n > 0 ? [{ texte: `${n} ${n === 1 ? un : plusieurs}`, provisoire: false }] : [];
	});
	if (composition.locaux_activite === 1) {
		parties.push({ texte: AVEC_UN_LOCAL_ACTIVITE, provisoire: false });
	} else if (composition.locaux_activite > 1) {
		parties.push({ texte: AVEC_DES_LOCAUX_ACTIVITE, provisoire: false });
	} else if (parties.length === 0) {
		parties.push({ texte: SANS_LOCAL_DECRIT, provisoire: false });
	}
	return parties;
}

/** « surface bâtie déclarée {s} m² », or its served absence in a sentence (IN-02 wording). */
export function ligneSurface(surface: number | null): string {
	if (surface == null) return `surface bâtie ${NON_RENSEIGNEE}`;
	return `surface bâtie déclarée ${formatBareNumber(FIELD_SURFACE, surface)}${NB}${displayUnit(FIELD_SURFACE) ?? ''}`;
}

function montant(fieldId: string, valeur: number): string {
	const unite = displayUnit(fieldId);
	return `${formatBareNumber(fieldId, valeur)}${unite ? `${NB}${unite}` : ''}`;
}

/**
 * Line 3 of a card: « {prix} · {prix au m²} » for a displayed price; null when the price is
 * withheld (the card shows the neutral badge) or not served.
 */
export function lignePrix(carte: CarteVente): string | null {
	if (!carte.prix_affiche || carte.prix == null) return null;
	const prix = montant(FIELD_PRIX, carte.prix);
	return carte.prix_m2 == null ? prix : `${prix} · ${montant(FIELD_PRIX_M2, carte.prix_m2)}`;
}

/** « à {d} m (à vol d'oiseau) » (UI-SPEC « Contrat de rédaction », Distance). */
export function ligneDistance(distance: number | null): string | null {
	if (distance == null) return null;
	return `à ${new Intl.NumberFormat('fr-FR').format(distance)}${NB}m (à vol d'oiseau)`;
}

/**
 * « Ventes {a} à {b} sur {N} » of the served page; null when the page carries no sale. C4 (04.5-07):
 * with a served VEFA count above zero, « …, dont {nombre_vefa} VEFA hors statistiques » (text of the
 * report 4.1-15, read in a block by Ianis, R10): the served count as read, never the VEFA cards of
 * the page nor a difference (D45-05).
 */
export function plageVentes(
	page: { page: number; par_page: number; ventes: CarteVente[] },
	total: number | null,
	libelle = 'Ventes',
	nombreVefa: number | null = null
): string | null {
	const n = page.ventes.length;
	if (!n || total == null) return null;
	const a = (page.page - 1) * page.par_page + 1;
	const plage = `${libelle} ${a} à ${a + n - 1} sur ${total}`;
	return nombreVefa != null && nombreVefa > 0 ? `${plage}, dont ${nombreVefa} VEFA hors statistiques` : plage;
}

/** Reasons of the withheld prices served on this page, read once in the panel of the section. */
function entreesMinimisation(cartes: CarteVente[]): EntreeVolet[] {
	const motifs = new Set(cartes.flatMap((carte) => (carte.minimisation ? [carte.minimisation.motif] : [])));
	const entrees: EntreeVolet[] = [];
	// One entry for the section (the term is the badge, keyed once): the served reasons in this order.
	const phrases: string[] = [];
	if (motifs.has('vente_isolee_recente')) phrases.push(PHRASE_VENTE_ISOLEE);
	if (motifs.has('isolement_indetermine')) phrases.push(PHRASE_ISOLEMENT_INDETERMINE);
	// A reason served later without a wording: provisional fallback, never invented.
	if ([...motifs].some((motif) => motif !== 'vente_isolee_recente' && motif !== 'isolement_indetermine')) {
		phrases.push(LIBELLE_NON_AFFICHABLE);
	}
	if (phrases.length) entrees.push({ terme: BADGE_PRIX_NON_AFFICHE, valeur: phrases.join(' ') });
	return entrees;
}

/** Source panel of the parcel sales: one `i` for the whole section (UI-SPEC « Volet ⓘ »). */
export function voletVentes(ventes: VentesParcelle): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(FIELD_NOMBRE));
	ajouter('Définition', definitionPublique(FIELD_NOMBRE));
	ajouter('Objet', 'Vente');
	ajouter('Rattachement', libelleRattachement('parcelle_du_point'));
	ajouter('Période', ventes.periode);
	ajouter('Source', ventes.source?.nom, ventes.source?.url ?? null);
	entrees.push(...entreesMinimisation(ventes.ventes));
	return entrees;
}

/**
 * Link to another page of a list, keeping every other parameter of the page (the other lists and
 * the criteria): `?{…}&ventes_page=2&vg=…`. A null value removes its parameter.
 */
export function lienPage(courants: URLSearchParams, changements: Record<string, string | null>, ancre = ''): string {
	const sp = new URLSearchParams(courants);
	for (const [cle, valeur] of Object.entries(changements)) {
		if (valeur === null) sp.delete(cle);
		else sp.set(cle, valeur);
	}
	return `?${sp.toString()}${ancre}`;
}

// --- Sales declared at the address (C37) ------------------------------------------------------

/** 04-DECISIONS-TEXTES row 69 (Ianis, 2026-10-10). */
export const TITRE_VENTES_ADRESSE = 'Ventes déclarées à cette adresse';
const FIELD_NOMBRE_ADRESSE = 'adresse.dvf.ventes_adresse.nombre';

/**
 * Served reasons of a list that is not searched, with a label and a sentence (rows 69 and 72; the
 * suffix sentence and `aucune_vente_rattachee` are row 73, provisional, C44). C3 (04.5-08): the page
 * asks the number and suffix rule; its two reasons are worded as the report 4.1-14 proposes them, to
 * be read in a block by Ianis (R10). `suffixe_non_distingue` (number-only rule, C37 « Au 2 seulement »)
 * stays for an answer of the former rule. `aucune_vente_rattachee` is served by the address
 * comparator only (C40), for an address to which no sale is attached.
 */
export const MOTIFS_VENTES_ADRESSE: Readonly<Record<string, { libelle: string; phrase: string }>> = {
	suffixe_sans_correspondance: {
		libelle: 'Suffixe sans correspondance',
		phrase: `Le suffixe de cette adresse ne correspond pas de façon sûre à un suffixe déclaré dans DVF${NNB}; les ventes déclarées à ce numéro n'y sont pas montrées.`
	},
	suffixe_non_importe: {
		libelle: 'Suffixe non disponible',
		phrase: `Les données DVF servies ont été importées sans le suffixe de l'adresse${NNB}; les ventes déclarées n'y sont pas encore distinguées par suffixe.`
	},
	suffixe_non_distingue: {
		libelle: 'Suffixe non distingué',
		phrase: `Les données DVF importées ne gardent pas le suffixe (bis, ter)${NNB}; les ventes déclarées à ce numéro sont montrées sur l'adresse sans suffixe.`
	},
	aucune_vente_rattachee: {
		libelle: 'Aucune vente rattachée',
		phrase: `Aucune vente de DVF n'est rattachée à cette adresse${NNB}; ce n'est pas la preuve qu'aucune vente n'y a eu lieu.`
	},
	sans_numero: {
		libelle: 'Numéro non comparable',
		phrase: `Le numéro de cette adresse ne se compare pas à celui des ventes déclarées dans DVF.`
	},
	adresse_indeterminee: {
		libelle: 'Adresse non déterminée',
		phrase: `L'adresse n'est pas établie de façon unique${NNB}; les ventes déclarées n'y sont pas recherchées.`
	}
};

/**
 * C4 (04.5-07): entry « VEFA » of the panel of the declared list when it serves VEFA; text of the report
 * 4.1-15 applied as written, to be read in a block by Ianis (R10).
 */
export const PHRASE_VOLET_VEFA = `Vente en l'état futur d'achèvement${NB}: un logement vendu avant d'être construit. Elle est montrée dans la liste mais n'entre dans aucune statistique, ni dans le nombre de ventes de cette adresse.`;

/**
 * A served reason of the declared sales as the page, the export and the comparator say it (Codex review
 * M1 of 04.5-08): its label and sentence when worded; a reason without wording (an API newer than the
 * page) is the site's neutral provisional label (« Valeur non affichable », as an unknown badge or
 * criterion), marked, without a sentence: its cause is not known, so never the glossary's « Non
 * publiée » and « La source ne publie pas… », never another reason. Null without a served reason.
 */
export function motifServiVentesAdresse(motif: string | null): { libelle: string; phrase: string | null; provisoire: boolean } | null {
	if (!motif) return null;
	return Object.hasOwn(MOTIFS_VENTES_ADRESSE, motif)
		? { ...MOTIFS_VENTES_ADRESSE[motif], provisoire: false }
		: { libelle: LIBELLE_NON_AFFICHABLE, phrase: null, provisoire: true };
}

/**
 * No sale attached to the address, with its source and period (row 73, provisional, C44): not a zero
 * of the source, the panel says why (first line of a sale only, sales without a BAN address).
 */
export function phraseZeroVentesAdresse(periode: string | null): string {
	return periode ? `Aucune vente rattachée à cette adresse dans DVF (${periode}).` : 'Aucune vente rattachée à cette adresse dans DVF.';
}

/** Source panel of the declared sales: the definition says how a sale is attached (row 69). */
export function voletVentesAdresse(ventes: VentesAdresse): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(FIELD_NOMBRE_ADRESSE));
	ajouter('Définition', definitionPublique(FIELD_NOMBRE_ADRESSE));
	ajouter('Objet', 'Vente');
	ajouter('Rattachement', libelleRattachement('adresse_declaree_vente'));
	ajouter('Période', ventes.periode);
	ajouter('Source', ventes.source?.nom, ventes.source?.url ?? null);
	// C4 (04.5-07): the VEFA of the list, said once when VEFA are served.
	if (ventes.nombre_vefa != null && ventes.nombre_vefa > 0) ajouter('VEFA', PHRASE_VOLET_VEFA);
	entrees.push(...entreesMinimisation(ventes.ventes));
	return entrees;
}

// --- The two relations of the sales (plan 04.5-06, UI-005; D45-05, D45-06) -------------------------

/**
 * UI-005: the list of the parcel and the list declared at the address are two relations; one sale
 * may be in both. Said once, before the declared list; no sale is compared, removed or recounted
 * (new label, read in a block by Ianis, R10).
 */
export const PHRASE_RECOUVREMENT_VENTES = `Une même vente peut figurer dans les deux listes${NB}: l'une rattache les ventes à la parcelle du point de l'adresse, l'autre à l'adresse déclarée dans la vente.`;

/** The overlap sentence is said when both lists serve sales: their served totals, nothing compared. */
export function recouvrementPossible(
	parcelle: Pick<VentesParcelle, 'etat' | 'total'> | null | undefined,
	declarees: Pick<VentesAdresse, 'etat' | 'total'> | null | undefined
): boolean {
	const servies = (liste: Pick<VentesParcelle | VentesAdresse, 'etat' | 'total'> | null | undefined) =>
		liste?.etat === 'value' && typeof liste.total === 'number' && liste.total > 0;
	return servies(parcelle) && servies(declarees);
}

// --- Similar sales (D-114) --------------------------------------------------------------------

export const TITRE_COMPARABLES = 'Ventes ressemblantes';
export const MESSAGE_PANNE_COMPARABLES = "Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes.";
export const RELANCER_COMPARABLES = 'Relancer la recherche des ventes';
export const PHRASE_SANS_TYPE = 'Choisissez un type pour lister les ventes ressemblantes.';
export const PHRASE_ZERO_COMPARABLES =
	'Aucune vente ne correspond à ces critères. Élargissez le rayon, la période ou la tolérance de surface.';
export const CHARGEMENT_COMPARABLES = 'Chargement des ventes ressemblantes…';
/** 04-DECISIONS-TEXTES row 33: no single point (badge « Position non déterminée »), no radius around it. */
export const PHRASE_COMPARABLES_SANS_POSITION = `La position de l'adresse n'est pas établie${NNB}; les ventes ressemblantes ne sont pas recherchées autour d'elle.`;
/** Row 34: a criterion outside the closed lists of the form (no read, never widened). */
export const PHRASE_CRITERE_NON_RECONNU = `Ce critère n'est pas reconnu${NNB}; choisissez une valeur dans la liste.`;
const FIELD_EFFECTIF = 'adresse.dvf.ventes_ressemblantes.effectif';

/** Closed lists of the GET form (UI-SPEC point 5): value in the URL → visible label. */
export const TYPES = [
	['maison', 'Maison'],
	['appartement', 'Appartement']
] as const;
export const TOLERANCES = [
	['10', `±${NB}10${NB}%`],
	['20', `±${NB}20${NB}%`],
	['30', `±${NB}30${NB}%`]
] as const;
export const RAYONS = [
	['250', `250${NB}m`],
	['500', `500${NB}m`],
	['1000', `1${NB}km`],
	['commune', 'toute la commune']
] as const;
export const PERIODES = [
	['1', `1${NB}an`],
	['3', `3${NB}ans`],
	['5', `5${NB}ans`]
] as const;
/** UI-SPEC defaults: ± 20 %, 500 m, 5 years; never a type (Q6). */
export const DEFAUTS = { tolerance: '20', rayon: '500', periode: '5' } as const;

const valeursDe = (liste: ReadonlyArray<readonly [string, string]>) => new Set(liste.map(([valeur]) => valeur));
const VALEURS = {
	type: valeursDe(TYPES),
	tolerance: valeursDe(TOLERANCES),
	rayon: valeursDe(RAYONS),
	periode: valeursDe(PERIODES)
};
const SURFACE_RE = /^[1-9]\d{0,3}$/;

/** Criteria read in the URL: what pre-fills the form, and whether they may be relayed. */
export interface CriteresLus {
	/** Chosen type; null when none (the form checks no radio, nothing is read). */
	type: 'maison' | 'appartement' | null;
	/** Values pre-filling the form: the URL's when recognized, else the defaults. */
	surface: string;
	tolerance: string;
	rayon: string;
	periode: string;
	/** A criterion outside the closed lists (or a malformed page of the list) is never relayed. */
	reconnus: boolean;
	/** The criteria to relay as read (present parameters only), when a type is chosen. */
	relayes: CriteresRelayes | null;
}

function lu(sp: URLSearchParams, cle: string): string | null {
	const valeur = sp.get(cle);
	return valeur === null || valeur === '' ? null : valeur;
}

/**
 * Reads the criteria of the similar sales in the URL, to pre-fill the GET form and to relay them.
 * No default type is ever assumed; the closed lists are checked here so that an unknown criterion
 * is said as such, never widened (D-114, T-04-05-03). Nothing is filtered here.
 */
export function lireCriteres(sp: URLSearchParams): CriteresLus {
	const type = lu(sp, 'type');
	const surface = lu(sp, 'surface');
	const tolerance = lu(sp, 'tolerance');
	const rayon = lu(sp, 'rayon');
	const periode = lu(sp, 'periode');
	const surfaceValide = surface === null || (SURFACE_RE.test(surface) && Number(surface) >= 9 && Number(surface) <= 1000);
	const reconnus =
		(type === null || VALEURS.type.has(type)) &&
		surfaceValide &&
		(tolerance === null || VALEURS.tolerance.has(tolerance)) &&
		(rayon === null || VALEURS.rayon.has(rayon)) &&
		(periode === null || VALEURS.periode.has(periode));
	const typeChoisi = type !== null && VALEURS.type.has(type) ? (type as 'maison' | 'appartement') : null;
	return {
		type: typeChoisi,
		surface: surface !== null && surfaceValide ? surface : '',
		tolerance: tolerance !== null && VALEURS.tolerance.has(tolerance) ? tolerance : DEFAUTS.tolerance,
		rayon: rayon !== null && VALEURS.rayon.has(rayon) ? rayon : DEFAUTS.rayon,
		periode: periode !== null && VALEURS.periode.has(periode) ? periode : DEFAUTS.periode,
		reconnus,
		relayes: type !== null && reconnus ? { type, surface, tolerance, rayon, periode } : null
	};
}

/** True when the page and the token of the similar sales have the form the API accepts. */
export function pageComparablesValide(sp: URLSearchParams): boolean {
	return parametresDePageValides(sp.get('vr_page'), sp.get('vg'));
}

const ENTIER = new Intl.NumberFormat('fr-FR');

function libellePerimetre(criteres: CriteresAppliques): string {
	if (criteres.perimetre === 'commune') return 'dans la commune';
	if (criteres.rayon_m === 1000) return `1${NB}km`;
	return criteres.rayon_m != null ? `${ENTIER.format(criteres.rayon_m)}${NB}m` : '';
}

/** « maisons · 72 à 108 m² · 1 km · 2021 → 2025 »: the applied criteria, as served. */
export function criteresServis(criteres: CriteresAppliques): string {
	const surface =
		criteres.surface_min != null && criteres.surface_max != null
			? `${ENTIER.format(criteres.surface_min)} à ${ENTIER.format(criteres.surface_max)}${NB}m²`
			: null;
	const periode =
		criteres.periode.debut != null && criteres.periode.fin != null ? `${criteres.periode.debut} → ${criteres.periode.fin}` : null;
	// A type the page does not know is not written (FRONT IN-04): never « appartements » by default.
	const type = criteres.type === 'maison' ? 'maisons' : criteres.type === 'appartement' ? 'appartements' : null;
	return [type, surface, libellePerimetre(criteres), periode]
		.filter(Boolean)
		.join(' · ');
}

/**
 * The count sentence, built from the served count and the served criteria only (UI-SPEC « Ventes
 * ressemblantes — effectif »): « {n} ventes ressemblantes · … », « 1 vente ressemblante · … »;
 * null when the count or the criteria are not served.
 */
export function phraseEffectif(servi: Pick<VentesRessemblantes, 'effectif' | 'criteres_appliques'>): string | null {
	if (servi.effectif == null || !servi.criteres_appliques) return null;
	const n = servi.effectif;
	const tete = n >= 2 ? `${ENTIER.format(n)}${NB}ventes ressemblantes` : `${n}${NB}vente ressemblante`;
	return `${tete} · ${criteresServis(servi.criteres_appliques)}`;
}

/**
 * Reserves of the similar sales: « Liste incomplète » when the radius reaches a departement not
 * hosted (C16): the served count is the hosted side only, never a complete one.
 */
export function reservesComparables(servi: Pick<VentesRessemblantes, 'liste_incomplete'>): string[] {
	return servi.liste_incomplete ? [LISTE_INCOMPLETE] : [];
}

/** The zero sentence, only for a zero measured over the whole radius (C16: never a partial zero). */
export function phraseZeroComparables(servi: Pick<VentesRessemblantes, 'effectif' | 'liste_incomplete'>): string | null {
	return servi.effectif === 0 && !servi.liste_incomplete ? PHRASE_ZERO_COMPARABLES : null;
}

/** « Rattachement » of a similar sale: the served radius, or the commune of the address. */
export function libelleRattachementComparables(servi: Pick<VentesRessemblantes, 'rattachement' | 'criteres_appliques'>): string | null {
	if (servi.rattachement === 'commune_de_l_adresse') return "Dans la commune de l'adresse";
	const rayon = servi.criteres_appliques?.rayon_m;
	return rayon != null ? `À moins de ${ENTIER.format(rayon)}${NB}m du point de l'adresse` : null;
}

/** Source panel of the similar sales: definition, criteria, relation, source, withheld prices. */
export function voletComparables(servi: VentesRessemblantes): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(FIELD_EFFECTIF));
	ajouter('Définition', definitionPublique(FIELD_EFFECTIF));
	ajouter('Objet', 'Vente');
	ajouter('Critères', servi.criteres_appliques ? criteresServis(servi.criteres_appliques) : null);
	ajouter('Rattachement', libelleRattachementComparables(servi));
	// The reserve of the row, with the reason why (owner review of 09/11).
	ajouter(LISTE_INCOMPLETE, servi.liste_incomplete ? RAISON_COMPARABLES_INCOMPLETS : null);
	ajouter('Source', servi.source?.nom, servi.source?.url ?? null);
	entrees.push(...entreesMinimisation(servi.ventes));
	return entrees;
}

// --- Similar sales prefilled from the documents of the address (C42) ---------------------------

/** What the form of the similar sales is prefilled with, and the visible line saying where from. */
export interface PreremplissageComparables {
	type: 'maison' | 'appartement' | null;
	surface: string | null;
	/**
	 * The visible line when the type alone is prefilled; null when a surface is, the line naming its
	 * document instead (UI-007, `provenanceSurface`).
	 */
	phrase: string | null;
	/**
	 * UI-007: the number of the document the surface is read from, `selection.document.numero` as
	 * served; null when no surface is prefilled (never deduced from the cards).
	 */
	numero: string | null;
	/** The served qualification of that selection (D-111): null for a valid document. */
	qualification: string | null;
}

/**
 * Provisional (04-DECISIONS-TEXTES row 71): the origin of a prefilled type. A prefilled surface says
 * its own document since UI-007 (04.5-06, `provenanceSurface`).
 */
export const ORIGINES_PREREMPLISSAGE = {
	type: "Type d'après les DPE de cette adresse."
} as const;

/**
 * C42: defaults of the form from the served list of the documents of the address, the visitor
 * keeping the hand (nothing is searched). The type only when EVERY document of the address is on
 * this page and declares the same type (house or flat); the surface only of the most recent
 * document as served by the API (D-111, a tie names none), when it is on this page, rounded to
 * the square metre the form accepts (9 to 1 000). Null when nothing is prefilled.
 */
export function preremplissageComparables(liste: DocumentsDpeAdresse | null | undefined): PreremplissageComparables | null {
	if (!liste || liste.etat !== 'value' || typeof liste.total !== 'number' || liste.documents.length === 0) return null;
	const types = new Set(liste.documents.map((doc) => doc.type_batiment));
	const [seul] = types;
	const complete = liste.total === liste.documents.length;
	const type = complete && types.size === 1 && (seul === 'maison' || seul === 'appartement') ? seul : null;
	const numero = liste.selection?.etat === 'unique' ? liste.selection.document?.numero : null;
	const plusRecent = numero ? liste.documents.find((doc) => doc.numero === numero) : undefined;
	const arrondie = plusRecent?.surface_habitable == null ? null : Math.round(plusRecent.surface_habitable);
	const surface = arrondie !== null && arrondie >= 9 && arrondie <= 1000 ? String(arrondie) : null;
	// UI-007: the document of a prefilled surface, as served; nothing when no surface is prefilled.
	const provenance =
		surface && numero && liste.selection
			? { numero, qualification: qualificatifSelection(liste.selection.motif) }
			: { numero: null, qualification: null };
	if (type && surface) return { type, surface, phrase: null, ...provenance };
	if (type) return { type, surface: null, phrase: ORIGINES_PREREMPLISSAGE.type, ...provenance };
	if (surface) return { type: null, surface, phrase: null, ...provenance };
	return null;
}

/**
 * UI-007 (plan 04.5-06; new label, read in a block by Ianis, R10): « Surface issue du DPE n° {numero} »,
 * linked to the page of the document, then « , le plus récent de cette adresse » and the served
 * qualification of the selection between brackets when it is not a valid document.
 */
export function provenanceSurface(numero: string, qualification: string | null): { lien: string; texte: string; suite: string } {
	return {
		lien: `/dpe/${encodeURIComponent(numero)}`,
		texte: `Surface issue du DPE n°${NB}${numero}`,
		suite: `, le plus récent de cette adresse${qualification ? ` (${qualification})` : ''}.`
	};
}
