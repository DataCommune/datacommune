// Model of the declared-documents section of the address page and of the source panel of a served
// value (04-18; UI-SPEC « /adresse/[id] » points 3 and 4, « Volet ⓘ — deltas », « Contrat de
// rédaction »; D-109, D-111, SEM-01, SEM-02). Pure module, zero Svelte imports.
//
// WHAT IT HOLDS: the wording of the list of `GET /v1/adresse/{ban_id}/dpe` (head sentence, cards,
// badges, groups, constat), the source panel entries (`volet`, shared with the comparators of
// `commune-comparison.ts`, `voletDocuments`, `voletAdresseBrute`, `voletConstat`) and the French
// spelling of served ISO days. The page renders what is served: the count, the most recent
// document, a tie, the reasons, the periods and the classes are the API's; nothing here picks,
// compares or recomputes one (D-51, D-103, D-111).
//
// STATES: a value without `etat=value` keeps its served state and reason, rendered by `StateValue`
// (SEM-01); a served key that the UI-SPEC does not word is rendered by the neutral label of the
// glossary for a value that cannot be displayed, never dropped and never read as a validity.

import type { EnergyClass } from '$lib/api/commune';
import type {
	AdresseDeclaree,
	ConstatD110,
	DocumentDpeCarte,
	DocumentsDpeAdresse,
	FicheAdressePublique,
	GroupeAdresseBrute,
	ValeurLieu
} from '$lib/api/adresses';
import type { EntreeVolet, KpiLigne } from './commune-kpi';
import {
	LIBELLE_NON_AFFICHABLE,
	LIBELLES_DOCUMENT_DPE,
	PHRASE_CONSERVATION_ADRESSE_DECLAREE,
	PHRASES_MOTIFS_ADRESSE_DECLAREE
} from './etats-libelles';
import { definitionPublique } from './explications';
import { LIBELLE_EN_CLAIR, enClairChamp } from './explications-simples';
import { displayUnit, formatBareNumber } from './formatters';

const NB = ' ';
const NNB = ' ';

/** Closed list of the DPE classes a tag can show. */
const CLASSES: readonly string[] = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

export function estClasse(value: string | null | undefined): value is EnergyClass {
	return typeof value === 'string' && CLASSES.includes(value);
}

// --- French dates of served ISO days ---------------------------------------------------------

const MOIS_ANNEE = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const DATE_LONGUE = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });

function jour(iso: string | null | undefined): Date | null {
	if (!iso || !/^\d{4}-\d{2}-\d{2}/.test(iso)) return null;
	const date = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
	return Number.isNaN(date.getTime()) ? null : date;
}

/** « mars 2024 » for a served ISO day; null when the day is not served. */
export function moisAnnee(iso: string | null | undefined): string | null {
	const date = jour(iso);
	return date ? MOIS_ANNEE.format(date) : null;
}

/** « 12 mars 2024 » for a served ISO day; null when the day is not served. */
export function dateLongue(iso: string | null | undefined): string | null {
	const date = jour(iso);
	return date ? DATE_LONGUE.format(date) : null;
}

// --- Source panel entries (SEM-02) -----------------------------------------------------------

/** « Rattachement » of the source panel, closed list of the UI-SPEC (« Volet ⓘ — deltas »). */
const LIBELLES_RATTACHEMENT: Readonly<Record<string, string>> = {
	adresse_saisie_diagnostiqueur: 'Adresse saisie par le diagnostiqueur (non vérifiée)',
	parcelle_du_point: "Parcelle qui contient le point de l'adresse",
	batiment_du_point: "Bâtiment qui contient le point de l'adresse",
	// 04-06, derived wording to be confirmed by the owner: the RNB identifier linked to the BAN key.
	batiment_rnb_de_l_adresse: 'Bâtiment que le RNB rattache à cette adresse',
	au_point: "Au point de l'adresse",
	eligibilite_adresse: 'Éligibilité publiée pour cette adresse',
	commune_reference: "Commune de l'adresse (référence)",
	// C37, provisional wording (04-DECISIONS-TEXTES row 69): the address written in the DVF declaration.
	adresse_declaree_vente: 'Adresse déclarée dans la vente (DVF)',
	// 04.1-13 (D-127), wording proposed by 04.1-REPORTS-UI.md « Plan 13 », applied as written by plan
	// 04.5-05: to be read again as a block by Ianis (D-119 point 5, R10).
	adresse_brute_exacte: 'Adresse saisie, rapprochée exactement de la BAN'
};

/**
 * Wording of a served relation; no entry for a key outside the list. A relation by radius is not
 * served since P-PARCELLE (the parcel of the point only): its branch, never reached, was removed
 * (IN-07 of the D-15 review).
 */
export function libelleRattachement(cle: string | null | undefined): string | null {
	if (!cle) return null;
	return Object.hasOwn(LIBELLES_RATTACHEMENT, cle) ? LIBELLES_RATTACHEMENT[cle] : null;
}

export interface Volet {
	fieldId: string;
	objet: string;
	valeur: ValeurLieu;
	lignes: KpiLigne[];
	/** Served period, already spelled for the panel. */
	periode?: string | null;
}

/** « {valeur} {unité} · {territoire} · {source}, {millésime} » (UI-SPEC « Volet ⓘ — deltas »). */
function texteReference(valeur: ValeurLieu): string | null {
	const reference = valeur.reference;
	if (!reference || reference.valeur == null) return null;
	const unite = displayUnit(valeur.field_id);
	const chiffre = `${formatBareNumber(valeur.field_id, reference.valeur)}${unite ? `${NB}${unite}` : ''}`;
	const territoire = reference.territoire.nom ?? reference.territoire.code_insee;
	const source = [reference.source?.nom, reference.millesime].filter(Boolean).join(', ');
	return [chiffre, territoire, source].filter(Boolean).join(' · ');
}

/** Source panel of one served value (UI-SPEC « Volet ⓘ — deltas »); read by the comparators. */
export function volet({ fieldId, objet, valeur, lignes, periode = null }: Volet): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(fieldId));
	ajouter('Définition', definitionPublique(fieldId));
	ajouter('Objet', objet);
	ajouter('Rattachement', libelleRattachement(valeur.rattachement));
	ajouter('Période', periode);
	ajouter('Unité', displayUnit(fieldId));
	ajouter('Source', valeur.source?.nom, valeur.source?.url ?? null);
	const absences = lignes.flatMap((ligne) =>
		ligne.etat.kind === 'absence' ? [{ libelle: ligne.libelle, etat: ligne.etat }] : []
	);
	if (absences.length) entrees.push({ terme: 'État et motif', absences });
	ajouter('Référence', texteReference(valeur), valeur.reference?.url ?? null);
	return entrees;
}

// --- Undetermined identity (04-DECISIONS-TEXTES, 2026-10-05, rows 7, 22 and 23) -------------------

/** Sentence under the title of an address whose BAN rows give contradictory positions (row 7). */
export const PHRASE_POSITIONS_CONTRADICTOIRES = `Les sources ne s'accordent pas sur la position de cette adresse${NNB}; les repères qui en dépendent ne sont pas calculés.`;
/** The BAN publishes no position for the address (row 22). */
export const PHRASE_POSITION_ABSENTE = `La position de cette adresse n'est pas publiée par la BAN${NNB}; les repères qui en dépendent ne sont pas calculés.`;
/** The BAN rows of the key disagree on their fields around one point (row 23). */
export const PHRASE_LIGNES_BAN_DIVERGENTES = `Les lignes de la BAN ne s'accordent pas sur cette adresse${NNB}; les repères qui en dépendent ne sont pas calculés.`;

/**
 * The sentence of an undetermined identity, by its served cause (rows 7, 22, 23): the parcel
 * reason `positions_contradictoires` or `position_absente`, or, the parcel being resolved, BAN
 * rows that disagree on their fields around one point (the API then serves no D-110 finding, API
 * WR-04). A parcel reason served later without a wording: no sentence.
 */
export function phraseIdentiteIndeterminee(identite: FicheAdressePublique['identite']): string | null {
	const parcelle = identite.parcelle;
	if (identite.etat !== 'indeterminee' || !parcelle) return null;
	if (parcelle.etat !== 'indeterminee') return PHRASE_LIGNES_BAN_DIVERGENTES;
	if (parcelle.motif === 'positions_contradictoires') return PHRASE_POSITIONS_CONTRADICTOIRES;
	if (parcelle.motif === 'position_absente') return PHRASE_POSITION_ABSENTE;
	return null;
}

const PHRASE_INDETERMINEE = 'Le document le plus récent ne peut pas être déterminé.';

/**
 * Qualifier of a served most recent document that is not valid (D-111). « expiré » and « ancienne
 * méthode » are the contract's; « remplacement non vérifié » and « validité non déterminée » are the
 * owner's (04-DECISIONS-TEXTES, 2026-10-05, rows 3 and 4); « remplacé selon l'ADEME » is the owner's
 * card badge in a sentence (« Choix de structure », row 24: one wording of a replaced document on
 * every surface, API WR-01 of the D-15 review). A reason served later without a wording is said by
 * the neutral label of the glossary, never dropped, never read as a validity.
 */
const QUALIFICATIFS: Readonly<Record<string, string | null>> = {
	document_valide: null,
	expire: 'expiré',
	ancienne_methode: 'ancienne méthode',
	remplace: "remplacé selon l'ADEME",
	remplacement_incertain: 'remplacement non vérifié',
	validite_inconnue: 'validité non déterminée'
};

export function qualificatifSelection(motif: string): string | null {
	return Object.hasOwn(QUALIFICATIFS, motif) ? QUALIFICATIFS[motif] : LIBELLE_NON_AFFICHABLE.toLowerCase();
}

// --- Declared documents (D-109) --------------------------------------------------------------

export const TITRE_DOCUMENTS = 'Documents DPE déclarés à cette adresse';
const FIELD_NOMBRE_DPE = 'adresse.dpe.nombre_declares';

/**
 * Instruction said before the first box of the list (UI-008, review of 2026-10-24, plan 04.5-05):
 * the purpose of the boxes, within the ceiling of the document comparator. New label, to be read
 * again as a block by Ianis (R10).
 */
export const CONSIGNE_COMPARAISON_DOCUMENTS = 'Sélectionnez 2 à 4 documents DPE pour les comparer';

/**
 * Count of the boxes the visitor checked, next to the instruction: never a figure of the list.
 * French agreement, 0 and 1 singular. New label, to be read again as a block by Ianis (R10).
 */
export function libelleCompteurSelection(coches: number): string {
	return `${coches} ${coches > 1 ? 'sélectionnés' : 'sélectionné'}`;
}

/** The one common line of the list (VN12), written once for every card. */
export function ligneCommuneDocuments(documents: DocumentsDpeAdresse): string {
	const debut = "Adresse saisie par le diagnostiqueur · aucun document n'est rattaché à un logement précis · ADEME";
	const date = dateLongue(documents.date_import);
	return date ? `${debut}, dernière importation réussie le ${date}` : debut;
}

/** Source panel of the section: one `i` button for the whole list (UI-SPEC « Volet ⓘ »). */
export function voletDocuments(documents: DocumentsDpeAdresse): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(FIELD_NOMBRE_DPE));
	ajouter('Définition', definitionPublique(FIELD_NOMBRE_DPE));
	ajouter('Objet', 'Document DPE');
	ajouter('Rattachement', libelleRattachement('adresse_saisie_diagnostiqueur'));
	ajouter('Source', documents.source?.nom, documents.source?.url ?? null);
	ajouter('Dernière importation réussie', dateLongue(documents.date_import));
	entrees.push(...entreesDesCartes(documents.documents));
	return entrees;
}

/**
 * Entries of a panel read from its cards: the meaning of their served badges, then the reasons of
 * their masked declared addresses with the conservation sentence (each once per panel).
 */
function entreesDesCartes(cartes: readonly DocumentDpeCarte[]): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	// The meaning of a label is read in the panel, once per section (UI-SPEC « Glossaire — deltas »).
	const servis = new Set(cartes.flatMap((doc) => doc.badges));
	for (const badge of ['ancienne_methode', 'expire', 'dates_incoherentes', 'etablissement_futur']) {
		const { libelle, phrase } = LIBELLES_DOCUMENT_DPE[badge];
		if (servis.has(badge) && phrase) entrees.push({ terme: libelle, valeur: phrase });
	}
	// A masked declared address is one short line on its card; the reasons served on this page are
	// read here, with the conservation sentence (04-DECISIONS-TEXTES, « Choix de structure »).
	const masquees = cartes.flatMap((doc) => (doc.adresse_declaree?.etat === 'masquee' ? [doc.adresse_declaree] : []));
	if (masquees.length) {
		const motifs = new Set(masquees.map((adresse) => adresse.motif));
		const phrases = Object.keys(PHRASES_MOTIFS_ADRESSE_DECLAREE)
			.filter((motif) => motifs.has(motif))
			.map((motif) => majuscule(PHRASES_MOTIFS_ADRESSE_DECLAREE[motif]));
		entrees.push({ terme: LIBELLE_ADRESSE_NON_AFFICHEE, valeur: [...phrases, PHRASE_CONSERVATION_ADRESSE_DECLAREE].join(' ') });
	}
	return entrees;
}

// --- Documents attached by their exact raw address (04.1-13, D-127; plan 04.5-05, C2) ----------
//
// A group of its own, after the list of the ADEME key: its title, its served total, its limit, its
// cards with their attachment badge and its own panel. Nothing of it enters the head sentence, the
// distribution, the most recent document or the D-110 finding of the ADEME key (D45-05, D45-06).
// The badge, the panel text and the attachment label are the texts proposed by 04.1-REPORTS-UI.md
// (« Plan 13 »), applied as written: to be read again as a block by Ianis (D-119 point 5, R10). The
// title and the sentences of the group are new labels of plan 04.5-05, listed for the same reading.

const FIELD_ADRESSE_BRUTE = 'adresse.dpe.documents_adresse_brute';

/** Badge of a card of the group (04.1-13, R10). */
export const BADGE_ADRESSE_BRUTE = "Rattaché par l'adresse saisie";

/**
 * Panel entry of the badge (04.1-13, R10). The words of the report; only the space before « ; » is
 * the narrow no-break space of the site typography (03-17 T-1, typographie-francaise.test.ts).
 */
export const EXPLICATION_ADRESSE_BRUTE =
	"L'identifiant d'adresse fourni par l'ADEME pour ce document ne correspond à aucune adresse de la Base Adresse Nationale importée. " +
	'Son adresse saisie par le diagnostiqueur (numéro, suffixe, voie et commune) désigne exactement cette adresse, et une seule. ' +
	`Ce rapprochement est fait par DataCommune, sans approximation${NNB}; il ne désigne aucun logement précis.`;

/** Title of the group (plan 04.5-05, R10). */
export const TITRE_GROUPE_ADRESSE_BRUTE = "Documents DPE rapprochés par l'adresse saisie";

/**
 * Badge of the served attachment of a card of the group: the badge of 4.1-13 for
 * `adresse_brute_exacte`; any other key is rendered by the neutral label of the glossary, marked
 * provisional, never by the wording of the ADEME key and never dropped.
 */
export function badgeRattachement(cle: string | null): { texte: string; provisoire: boolean } {
	return cle === 'adresse_brute_exacte' ? { texte: BADGE_ADRESSE_BRUTE, provisoire: false } : { texte: LIBELLE_NON_AFFICHABLE, provisoire: true };
}

/**
 * Head sentence of the group: its served total, said apart from the count of the ADEME key. A group
 * not computed for the served import says so, never with a number; a `value` without a readable
 * total says it cannot be read.
 */
export function phraseGroupeAdresseBrute(groupe: GroupeAdresseBrute): string {
	if (groupe.etat === 'not_available') return "Le rapprochement par l'adresse saisie n'est pas calculé pour cette importation des données DPE.";
	if (groupe.total == null) return "Le nombre de DPE rapprochés par l'adresse saisie n'a pas pu être lu.";
	if (groupe.total === 0) return 'Aucun DPE rapproché de cette adresse par son adresse saisie.';
	if (groupe.total === 1) return "1 DPE rapproché de cette adresse par son adresse saisie. Il n'est pas compté parmi les DPE déclarés à cette adresse.";
	return `${groupe.total} DPE rapprochés de cette adresse par leur adresse saisie. Ils ne sont pas comptés parmi les DPE déclarés à cette adresse.`;
}

/**
 * The limit of the group (R6, « dix sur N », Ianis 2026-10-24): the API serves its most recent cards
 * only; when its served total exceeds them, the page says how many of how many are shown. The first
 * number is the count of the cards served, the second the served total: nothing else is computed.
 */
export function limiteGroupeAdresseBrute(groupe: GroupeAdresseBrute): string | null {
	const affiches = groupe.documents.length;
	if (groupe.etat !== 'value' || groupe.total == null || affiches === 0 || groupe.total <= affiches) return null;
	return affiches === 1 ? `Le plus récent sur ${groupe.total} est affiché.` : `Les ${affiches} plus récents sur ${groupe.total} sont affichés.`;
}

/**
 * Panel of the group, its own `i` button: the definition of the contract, the object, the
 * attachment, the badge explained when a card carries it, the source and the last import of the
 * list, then the meanings read from its cards. The panel of the ADEME list is left unchanged.
 */
export function voletAdresseBrute(documents: DocumentsDpeAdresse): EntreeVolet[] {
	const cartes = documents.adresseBrute?.documents ?? [];
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(FIELD_ADRESSE_BRUTE));
	ajouter('Définition', definitionPublique(FIELD_ADRESSE_BRUTE));
	ajouter('Objet', 'Document DPE');
	ajouter('Rattachement', libelleRattachement('adresse_brute_exacte'));
	if (cartes.some((carte) => carte.rattachement === 'adresse_brute_exacte')) ajouter(BADGE_ADRESSE_BRUTE, EXPLICATION_ADRESSE_BRUTE);
	ajouter('Source', documents.source?.nom, documents.source?.url ?? null);
	ajouter('Dernière importation réussie', dateLongue(documents.date_import));
	entrees.push(...entreesDesCartes(cartes));
	return entrees;
}

/** A reason is written to follow « Adresse déclarée non affichée : »; alone it starts a sentence. */
function majuscule(phrase: string): string {
	return phrase.charAt(0).toUpperCase() + phrase.slice(1);
}

const LIBELLE_ADRESSE_NON_AFFICHEE = 'Adresse déclarée non affichée';

// --- D-110 finding (UI-SPEC « Politique d'affichage DPE », « D-110 retenue », 2026-10-02) ------

export const TITRE_CONSTAT_D110 = `Une adresse, un bâtiment, un DPE${NB}: les éléments concordent`;
export const LIMITE_CONSTAT_D110 = "Ce constat ne prouve pas l'identité d'un logement.";

/** Wording of each served criterion (UI-SPEC « D-110 retenue », body of the notice). */
export const LIBELLES_CRITERES_D110: Record<string, string> = {
	maison: 'maison',
	document_unique_cle: 'document unique à la clé BAN',
	concordance_stricte: "concordance stricte de l'adresse brute",
	batiment_unique_parcelle_du_point: 'bâtiment unique sur la parcelle du point BAN',
	// 04-DECISIONS-TEXTES, 2026-10-05, row 8: no column name in the public text; BD TOPO stays in « Sources ».
	annexes_exclues_usage_inconnu_compte: "annexes déclarées exclues, bâtiment d'usage inconnu compté"
};

/**
 * The served criteria in the served order, each with its wording. A criterion the UI-SPEC does
 * not word is kept and rendered by the neutral label of the glossary, marked provisional (stop
 * point for the owner); nothing is added, dropped or checked here.
 */
export function criteresConstat(constat: ConstatD110): { texte: string; provisoire: boolean }[] {
	return constat.criteres.map((cle) =>
		Object.hasOwn(LIBELLES_CRITERES_D110, cle)
			? { texte: LIBELLES_CRITERES_D110[cle], provisoire: false }
			: { texte: LIBELLE_NON_AFFICHABLE, provisoire: true }
	);
}

/** Source panel of the finding: method (the served criteria), sources, limit. */
export function voletConstat(constat: ConstatD110): EntreeVolet[] {
	const entrees: EntreeVolet[] = [
		{ terme: 'Méthode', valeur: criteresConstat(constat).map((critere) => critere.texte).join(' · ') }
	];
	const sources = constat.sources.map((source) => source.nom).join(' · ');
	if (sources) entrees.push({ terme: 'Sources', valeur: sources });
	entrees.push({ terme: 'Limite', valeur: LIMITE_CONSTAT_D110 });
	return entrees;
}

/**
 * Line 4 of a card (UI-SPEC « /adresse/[id] » point 4, P-ADR-01): the served text as typed, between
 * French quotes, or the short line « Adresse déclarée non affichée » of a masked address, whose
 * served reason is read in the panel of the section (04-DECISIONS-TEXTES, « Choix de structure »,
 * 160 px cap of a card). Nothing for a strictly concordant document (`null` served), an absent
 * text, or a published state without text: no text is ever built, corrected or replaced by the BAN
 * address here.
 */
export function ligneAdresseDeclaree(adresse: AdresseDeclaree | null): string | null {
	if (adresse?.etat === 'publiee') return adresse.texte ? `Adresse déclarée${NB}: «${NNB}${adresse.texte}${NNB}»` : null;
	return adresse?.etat === 'masquee' ? LIBELLE_ADRESSE_NON_AFFICHEE : null;
}

/**
 * Served badge of a card → its public label (UI-SPEC « Politique d'affichage DPE », owner's wording
 * of 2026-10-05 for `remplace`, `dates_incoherentes` and `etablissement_futur`). A badge served
 * later without a wording is rendered by the neutral label of the glossary, marked provisional,
 * never dropped.
 */
export function badgeProvisoire(badge: string): boolean {
	return !Object.hasOwn(LIBELLES_DOCUMENT_DPE, badge);
}

export function libelleBadge(badge: string): string {
	return badgeProvisoire(badge) ? LIBELLE_NON_AFFICHABLE : LIBELLES_DOCUMENT_DPE[badge].libelle;
}

export const CLASSES_REPARTITION: readonly EnergyClass[] = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

/** Name of the served A→G distribution of the list, on the page (accessible name) and in the export. */
export const LIBELLE_REPARTITION = 'Répartition des documents par classe énergie';

/** Sentence of the served signal of several dwellings, on the page and in the export. */
export const PHRASE_SIGNAL_PLUSIEURS_LOGEMENTS =
	'Cette adresse peut compter plusieurs logements (copropriété immatriculée au Registre national des copropriétés).';

/** « Maison » for a served type; the served text, first letter upper-cased. */
export function libelleType(type: string | null): string | null {
	return type ? type.charAt(0).toUpperCase() + type.slice(1) : null;
}

const SURFACE = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 });

/** « 92 m² » for a served surface. */
export function libelleSurface(surface: number | null): string | null {
	return surface == null ? null : `${SURFACE.format(surface)}${NB}m²`;
}

/**
 * Head sentence of the documents section (contrat de rédaction, « En-tête DPE (D-109) », and the
 * tie / undetermined forms of DPE-01). Every part is served: the count, the selection, its date,
 * its classes and its reason; nothing is picked or compared here.
 */
export function phraseEnTeteDpe(documents: DocumentsDpeAdresse): string {
	// A count not served is never read as zero (IN-01 of the D-15 review, SEM-01): the outage sentence.
	if (documents.total == null) return MESSAGE_PANNE_DOCUMENTS;
	const total = documents.total;
	const selection = documents.selection;
	if (total === 0) {
		const date = dateLongue(documents.date_import);
		const base = date ? `dans la base ADEME importée le ${date}` : 'dans la base ADEME';
		return `Aucun DPE déclaré à cette adresse ${base}. Cela ne prouve pas qu'aucun DPE n'a été réalisé.`;
	}
	const debut = total === 1 ? '1 DPE déclaré à cette adresse' : `${total} DPE déclarés à cette adresse`;
	if (selection?.etat === 'unique' && selection.document) {
		const qualificatif = qualificatifSelection(selection.motif);
		const classe = `classe ${selection.document.etiquette_dpe ?? NON_RENSEIGNEE}, ${moisAnnee(selection.date) ?? `date ${NON_RENSEIGNEE}`}${qualificatif ? ` (${qualificatif})` : ''}`;
		return total === 1 ? `${debut} · ${classe}` : `${debut} · le plus récent${NB}: ${classe}`;
	}
	if (selection?.etat === 'egalite') {
		const k = selection.documents_ex_aequo.length;
		const classes = selection.documents_ex_aequo.map((doc) => doc.etiquette_dpe ?? NON_RENSEIGNEE).join(', ');
		const date = dateLongue(selection.date) ?? NON_RENSEIGNEE;
		return `${debut} · ${k} documents partagent la date la plus récente${NB}: ${date} · classes ${classes}`;
	}
	if (selection?.etat === 'indeterminee') return `${debut}. ${PHRASE_INDETERMINEE}`;
	return debut;
}

/** Outage sentence of the section, also said when the served count cannot be read (IN-01). */
export const MESSAGE_PANNE_DOCUMENTS = "Les documents DPE n'ont pas pu être lus. Le reste de la page reste affiché.";

/**
 * « Non renseigné » of the glossary, inside a sentence (IN-02 of the D-15 review): lower case, and
 * agreed with « classe », « date ». The badge form stays « Non renseigné ».
 */
export const NON_RENSEIGNEE = 'non renseignée';
export const NON_RENSEIGNE = 'non renseigné';

/** « Établi le {date} » of a card, or its served absence in a sentence (IN-02). */
export function ligneDates(etablissement: string | null, finValidite: string | null): string {
	const etabli = dateLongue(etablissement);
	const fin = dateLongue(finValidite);
	return [
		etabli ? `Établi le ${etabli}` : `Date d'établissement ${NON_RENSEIGNEE}`,
		fin ? `valable jusqu'au ${fin}` : `fin de validité ${NON_RENSEIGNEE}`
	].join(' · ');
}

/** « Documents {a} à {b} sur {N} » of the served page; null when the page carries no document. */
export function plageDocuments(documents: DocumentsDpeAdresse): string | null {
	const n = documents.documents.length;
	if (!n || documents.total == null) return null;
	const a = (documents.page - 1) * documents.par_page + 1;
	return `Documents ${a} à ${a + n - 1} sur ${documents.total}`;
}
