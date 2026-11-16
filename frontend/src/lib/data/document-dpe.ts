// Model of the DPE document page (04-04; UI-SPEC « /dpe/[numero] — page document », « Les quatre
// états », « Contrat de rédaction »; LIEU-03, D-102, D-111, SEM-01, SEM-02). Pure module, zero
// Svelte imports.
//
// WHAT IS READ: `GET /v1/dpe/{numero}` as projected by `documents-dpe.ts`. Every value, state,
// date and axis is the API's; nothing here computes a date, a validity, a class or a range
// (D-51, D-111). The wordings absent from the UI-SPEC are the owner's of 2026-10-05
// (04-DECISIONS-TEXTES, « Textes et choix de 04-04 »). A key served later without a wording is
// rendered by the neutral label of the glossary, marked provisional, never dropped.

import type { AdresseDeclaree, ValeurLieu } from '$lib/api/adresses';
import type { DocumentDpePublic, MethodeDocument, RepereEtiquette } from '$lib/api/documents-dpe';
import { dateLongue, estClasse, libelleRattachement, libelleSurface, libelleType } from './adresse-fiche';
import type { EntreeVolet, EtatKpi, KpiLigne, SourceKpi, ValeurServie } from './commune-kpi';
import { DEPARTEMENTS } from './departements';
import {
	LIBELLE_NON_AFFICHABLE,
	LIBELLES_DOCUMENT_DPE,
	PHRASES_MOTIFS_ADRESSE_DECLAREE,
	phraseEtat
} from './etats-libelles';
import { definitionPublique } from './explications';
import { LIBELLE_EN_CLAIR, enClairChamp } from './explications-simples';
import { displayUnit, formatBareNumber } from './formatters';
import { MESSAGE_PANNE_ADRESSE, libelleDepartement, libelleVoie } from './recherche-lieux';

// No-break space (before « : », between a number and its unit) and narrow no-break space (inside
// « », before « ; »), written by their code points so that no editor turns them into spaces.
const NB = String.fromCharCode(0x00a0);
const NNB = String.fromCharCode(0x202f);

export const SURTITRE_DOCUMENT = 'Document DPE';

/** « DPE n° {numero} »: the source number as served, never a rank (D-102). */
export function titreDocument(numero: string): string {
	return `DPE n°${NB}${numero}`;
}

const lienDocument = (numero: string) => `/dpe/${encodeURIComponent(numero)}`;

// --- Breadcrumb and action (UI-SPEC points 1 and 2) --------------------------------------------

export interface EtapeAriane {
	libelle: string;
	/** Link of the step; null for the current page. */
	href: string | null;
}

/** The BAN address the source retains, when the API serves one with its key. */
function adresseRetenue(document: DocumentDpePublic) {
	const ban = document.adresse_ban;
	return ban?.etat === 'retenue' && ban.ban_id ? { ...ban, ban_id: ban.ban_id } : null;
}

/** Target of « Voir l'adresse »: only for a BAN address served by the API. */
export function lienAdresse(document: DocumentDpePublic): string | null {
	const ban = adresseRetenue(document);
	return ban ? `/adresse/${encodeURIComponent(ban.ban_id)}` : null;
}

/** Root of the breadcrumb: « Accueil » on every page (T6, decided by Ianis on 2026-11-10). */
export const RACINE_ARIANE: EtapeAriane = { libelle: 'Accueil', href: '/' };

/**
 * « Accueil / {Commune} / {numéro} {voie} / DPE n° {numero} » when the API serves the BAN
 * address of the document; otherwise « Accueil / DPE n° {numero} ».
 */
export function filAriane(document: DocumentDpePublic): EtapeAriane[] {
	const ban = adresseRetenue(document);
	const courant = { libelle: titreDocument(document.numero), href: null };
	if (!ban) return [RACINE_ARIANE, courant];
	const codeInsee = ban.commune?.code_insee || ban.ban_id.split('_', 1)[0];
	return [
		RACINE_ARIANE,
		{ libelle: ban.commune?.nom ?? `Commune INSEE ${codeInsee}`, href: `/commune/${encodeURIComponent(codeInsee)}` },
		// Same wording as the breadcrumb of the address page (« Adresse » when its rows disagree).
		{ libelle: libelleVoie(ban.numero, ban.rep, ban.nom_voie) || 'Adresse', href: lienAdresse(document) },
		courant
	];
}

// --- Identity line (UI-SPEC point 1) ---------------------------------------------------------

/** One segment of a line (every wording is decided: no provisional mark, IN-07 of the D-15 review). */
export interface SegmentIdentite {
	texte: string;
}

/**
 * Served method → its wording: « ancienne méthode » (UI-SPEC), « méthode actuelle (depuis juillet
 * 2021) » and « méthode non déterminée » (owner, rows 1 and 2), written by « Provenance et validité »
 * and the comparator. An unknown served method is projected as `inconnue` (`documents-dpe.ts`): never
 * read as current.
 */
const LIBELLES_METHODE: Readonly<Record<MethodeDocument, string>> = {
	ancienne: LIBELLES_DOCUMENT_DPE.ancienne_methode.libelle.toLowerCase(),
	actuelle: 'méthode actuelle (depuis juillet 2021)',
	inconnue: 'méthode non déterminée'
};

/** The same wording as the value of « Méthode » in « Provenance et validité », first letter up. */
export function methodeProvenance(methode: MethodeDocument): string {
	if (methode === 'ancienne') return LIBELLES_DOCUMENT_DPE.ancienne_methode.libelle;
	const texte = LIBELLES_METHODE[methode];
	return texte.charAt(0).toUpperCase() + texte.slice(1);
}

/**
 * « Établi le {date} · valable jusqu'au {date} »: each date only when served; nothing is derived from
 * another date. The method is not repeated here (P4, 2026-11-10): « Provenance et validité » says it,
 * and the badge « Ancienne méthode » flags an old one.
 */
export function ligneIdentite(document: DocumentDpePublic): SegmentIdentite[] {
	const segments: SegmentIdentite[] = [];
	const etabli = dateLongue(document.dates?.etablissement);
	const fin = dateLongue(document.dates?.fin_validite);
	if (etabli) segments.push({ texte: `Établi le ${etabli}` });
	if (fin) segments.push({ texte: `valable jusqu'au ${fin}` });
	return segments;
}

/**
 * Badges of the identity line: neutral facts on the document, never a `warn` (UI-SPEC « Couleur »).
 * « Expiré » reads the served calendar axis, whatever the replacement (D-111: distinct axes).
 */
export function badgesIdentite(document: DocumentDpePublic): string[] {
	return badgesDocument(document.methode, document.statut.validite);
}

/** The same badges from the served method and calendar axis (a column of the comparator, 04-10). */
export function badgesDocument(methode: MethodeDocument, validite: string | null): string[] {
	const badges: string[] = [];
	if (methode === 'ancienne') badges.push(LIBELLES_DOCUMENT_DPE.ancienne_methode.libelle);
	if (validite === 'expired') badges.push(LIBELLES_DOCUMENT_DPE.expire.libelle);
	return badges;
}

// --- Landmarks (UI-SPEC point 3) ---------------------------------------------------------------

/** One landmark of the document, rendered by `Kpi` like a landmark of the address page. */
export interface KpiDocument {
	id: 'energie' | 'climat' | 'logement' | 'cout';
	rang: 1 | 2 | 3 | 4;
	libelle: string;
	lignes: KpiLigne[];
	source: SourceKpi | null;
	volet: EntreeVolet[];
}

type EtatAffiche = Exclude<EtatKpi, { kind: 'absence-non-declaree' }>;

function etatDe(valeur: ValeurLieu, texte: string | null = null, unite?: string | null): EtatAffiche {
	if (valeur.etat === 'value' && valeur.valeur !== null) {
		return {
			kind: 'valeur',
			texte: texte ?? String(valeur.valeur),
			unite: unite === undefined ? displayUnit(valeur.field_id) : unite
		};
	}
	// `value` without a value is not a value: rendered as the outage of its landmark (SEM-01).
	const etat = valeur.etat === 'value' ? 'temporary_error' : valeur.etat;
	const absence: EtatAffiche = { kind: 'absence', etat, motif: valeur.motif ?? '', effectif: null };
	// « not reported » speaks of the document (UI-SPEC « Glossaire — deltas »).
	const aide = etat === 'not_reported' ? phraseEtat(etat, '', null, 'document') : null;
	if (aide) absence.aide = aide;
	return absence;
}

function brut(valeur: ValeurLieu, etat: EtatKpi): ValeurServie {
	return {
		fieldId: valeur.field_id,
		valeur: etat.kind === 'valeur' ? JSON.stringify(valeur.valeur) : 'null',
		etat: etat.kind === 'absence' ? { kind: 'absence', etat: etat.etat, motif: etat.motif } : { kind: 'valeur' }
	};
}

function nombre(valeur: ValeurLieu): string | null {
	return typeof valeur.valeur === 'number' ? formatBareNumber(valeur.field_id, valeur.valeur) : null;
}

function ligne(
	id: string,
	valeur: ValeurLieu,
	etat: EtatKpi = etatDe(valeur, nombre(valeur)),
	ligneCourte: string | null = null
): KpiLigne {
	// The short line describes a served figure: an absence carries its own badge instead.
	const fenetre = etat.kind === 'valeur' ? ligneCourte : null;
	return { id, fieldId: valeur.field_id, libelle: null, etat, effectif: null, fenetre, brut: brut(valeur, etat) };
}

/**
 * Short lines under the landmarks (owner, 2026-10-05, row 12): ① and ② read the per-m² values of the
 * document for its five uses (« par m² · 5 usages », decided for when the per-m² values are served:
 * plan 04.5-12, C1; the yearly totals are in the detail); ③ has none; ④ is a simulation at standard
 * occupancy.
 */
export const LIGNE_COURTE_M2 = 'par m² · 5 usages';
export const LIGNE_COURTE_COUT = 'estimation à occupation standard';

function sourceKpi(valeur: ValeurLieu): SourceKpi | null {
	return valeur.source ? { nom: valeur.source.nom, url: valeur.source.url } : null;
}

/**
 * « EP » of the unit « kWhEP », said in plain words where the unit is read (P5, 2026-11-10). The
 * acronym list of the socle cannot take it: « kWhEP » is one glued word (socle § 6).
 * Proposed, to be validated by Ianis.
 */
export const PHRASE_ENERGIE_PRIMAIRE =
	"Énergie primaire : l'énergie prise à la source, avant sa transformation et son transport. kWhEP se lit kilowattheures d'énergie primaire.";

/**
 * Source panel of one landmark: « En clair » first (P3, the owner's review of 09/11), then definition,
 * object, relation, unit (with « EP » said in plain words), source, state, method.
 */
function volet(valeurs: ValeurLieu[], lignes: KpiLigne[], methode: MethodeDocument): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined, url: string | null = null) => {
		if (texte) entrees.push(url ? { terme, valeur: texte, url } : { terme, valeur: texte });
	};
	// One entry per term (the panel keys its entries by term): the plain sentences, definitions and
	// units of a landmark of several values are joined.
	const joindre = (textes: (string | null)[], separateur: string) => textes.filter(Boolean).join(separateur) || null;
	ajouter(LIBELLE_EN_CLAIR, joindre(valeurs.map((valeur) => enClairChamp(valeur.field_id)), ' '));
	ajouter('Définition', joindre(valeurs.map((valeur) => definitionPublique(valeur.field_id)), ' '));
	ajouter('Objet', SURTITRE_DOCUMENT);
	ajouter('Rattachement', libelleRattachement(valeurs[0]?.rattachement));
	const unite = joindre(valeurs.map((valeur) => displayUnit(valeur.field_id)), ' · ');
	ajouter('Unité', unite);
	if (unite?.includes('kWhEP')) ajouter('EP', PHRASE_ENERGIE_PRIMAIRE);
	ajouter('Source', valeurs[0]?.source?.nom, valeurs[0]?.source?.url ?? null);
	const absences = lignes.flatMap((l) => (l.etat.kind === 'absence' ? [{ libelle: l.libelle, etat: l.etat }] : []));
	if (absences.length) entrees.push({ terme: 'État et motif', absences });
	// The method is explained in the panel (UI-SPEC point 3), with the contract's sentence only.
	if (methode === 'ancienne') {
		ajouter(LIBELLES_DOCUMENT_DPE.ancienne_methode.libelle, LIBELLES_DOCUMENT_DPE.ancienne_methode.phrase);
	}
	return entrees;
}

/**
 * ① and ②: the served class as a tag next to the served figure, with its real unit. A class that is
 * not a served A-G value gets its own line « Classe », rendered by its state and listed in the panel:
 * a missing class is said, never dropped (FRONT WR-01 of the D-15 review, SEM-01). The figure is the
 * per-m² value published by the ADEME (`mesure_m2`, C1) as served: a missing one is « Non renseigné »,
 * never the total divided by the surface (D45-05); the yearly total is in the detail.
 */
function repereEtiquette(
	document: DocumentDpePublic,
	id: 'energie' | 'climat',
	rang: 1 | 2,
	libelle: string,
	etiquette: RepereEtiquette
): KpiDocument {
	const mesure = etiquette.mesure_m2;
	const classe = etiquette.classe.etat === 'value' ? etiquette.classe.valeur : null;
	const premiere: KpiLigne = {
		...ligne(`${id}-mesure`, mesure, undefined, LIGNE_COURTE_M2),
		etiquette: typeof classe === 'string' && estClasse(classe) ? classe : null
	};
	const lignes = premiere.etiquette ? [premiere] : [premiere, { ...ligne(`${id}-classe`, etiquette.classe), libelle: 'Classe' }];
	return {
		id,
		rang,
		libelle,
		lignes,
		source: sourceKpi(mesure),
		volet: volet([etiquette.classe, mesure], lignes, document.methode)
	};
}

/**
 * ③ « type · surface habitable déclarée » as served. A value the document leaves empty is not
 * written here; its own « Non renseigné » is in « Ce que déclare le document ».
 */
function repereLogement(document: DocumentDpePublic, logement: { type_batiment: ValeurLieu; surface: ValeurLieu }): KpiDocument {
	const type =
		logement.type_batiment.etat === 'value' && typeof logement.type_batiment.valeur === 'string'
			? libelleType(logement.type_batiment.valeur)
			: null;
	const surface =
		logement.surface.etat === 'value' && typeof logement.surface.valeur === 'number' ? libelleSurface(logement.surface.valeur) : null;
	const texte = [type, surface].filter(Boolean).join(' · ');
	const servie = type ? logement.type_batiment : logement.surface;
	const etat: EtatKpi = texte ? { kind: 'valeur', texte, unite: null } : etatDe(servie);
	const premiere = ligne('logement-description', servie, etat);
	return {
		id: 'logement',
		rang: 3,
		libelle: 'Type et surface déclarés',
		lignes: [premiere],
		source: sourceKpi(servie),
		volet: volet([logement.type_batiment, logement.surface], [premiere], document.methode)
	};
}

/** ④ « Coût annuel estimé des énergies »: the served value in €/an; no range is made up. */
function repereCout(document: DocumentDpePublic, cout: ValeurLieu): KpiDocument {
	const premiere = ligne('cout-annuel', cout, undefined, LIGNE_COURTE_COUT);
	return {
		id: 'cout',
		rang: 4,
		libelle: 'Coût annuel estimé des énergies',
		lignes: [premiere],
		source: sourceKpi(cout),
		volet: volet([cout], [premiere], document.methode)
	};
}

/** The four landmarks of the document as served; empty for a retired document (no description). */
export function reperesDocument(document: DocumentDpePublic): KpiDocument[] {
	const reperes = document.reperes;
	if (!reperes) return [];
	return [
		repereEtiquette(document, 'energie', 1, 'Étiquette énergie', reperes.etiquette_energie),
		repereEtiquette(document, 'climat', 2, 'Étiquette climat', reperes.etiquette_climat),
		repereLogement(document, reperes.logement_decrit),
		repereCout(document, reperes.cout_annuel)
	];
}

// --- « Ce que déclare le document » (UI-SPEC point 4) -----------------------------------------

/** Title of the section of the characteristics (UI-SPEC point 4), the detail of the page. */
export const TITRE_CARACTERISTIQUES = 'Ce que déclare le document';

/** Labels of the served characteristics, from the list of the UI-SPEC point 4. */
const LIBELLES_CARACTERISTIQUES: Readonly<Record<string, string>> = {
	'dpe.document.type_batiment': 'Type de bâtiment',
	'dpe.document.surface_habitable': 'Surface habitable',
	'dpe.document.periode_construction': 'Période de construction',
	'dpe.document.energie_chauffage': 'Énergie de chauffage',
	'dpe.document.installation_chauffage': 'Installation de chauffage',
	'dpe.document.isolation': 'Isolation'
};

/**
 * The yearly totals of the labels, read in `reperes.etiquette_*.mesure` and written after the served
 * characteristics (report 4.1-12, plan 04.5-12): labels of the report, to be read again as a block by
 * Ianis (R10).
 */
const TOTAUX_ANNUELS: readonly { etiquette: 'etiquette_energie' | 'etiquette_climat'; libelle: string }[] = [
	{ etiquette: 'etiquette_energie', libelle: "Consommation annuelle d'énergie primaire" },
	{ etiquette: 'etiquette_climat', libelle: 'Émissions annuelles de gaz à effet de serre' }
];

export interface Caracteristique {
	fieldId: string;
	libelle: string;
	/** The field has no label of the UI-SPEC: the neutral label is rendered, marked provisional. */
	provisoire: boolean;
	etat: EtatAffiche;
	/** A served number (with its unit): its digits never break; a text wraps. */
	numerique: boolean;
	/** The displayed line with its raw served value (inventory, Source panel). */
	ligne: KpiLigne;
	source: SourceKpi | null;
	/** Source panel of the row: definition, object, relation, unit, source, state (D45-08). */
	volet: EntreeVolet[];
}

function caracteristique(document: DocumentDpePublic, valeur: ValeurLieu, libelle: string | null, etat: EtatAffiche): Caracteristique {
	const affichee = { ...ligne(valeur.field_id, valeur, etat), libelle: libelle ?? LIBELLE_NON_AFFICHABLE };
	return {
		fieldId: valeur.field_id,
		libelle: libelle ?? LIBELLE_NON_AFFICHABLE,
		provisoire: libelle === null,
		etat,
		numerique: typeof valeur.valeur === 'number',
		ligne: affichee,
		source: sourceKpi(valeur),
		volet: volet([valeur], [affichee], document.methode)
	};
}

/** Text of a served characteristic as « Ce que déclare le document » writes it (also the comparator). */
export function texteCaracteristique(valeur: ValeurLieu): string | null {
	if (valeur.etat !== 'value' || valeur.valeur === null) return null;
	if (valeur.field_id === 'dpe.document.surface_habitable' && typeof valeur.valeur === 'number') return libelleSurface(valeur.valeur);
	if (valeur.field_id === 'dpe.document.type_batiment' && typeof valeur.valeur === 'string') return libelleType(valeur.valeur);
	return String(valeur.valeur);
}

/**
 * Every served characteristic, in the served order, then the yearly totals with their unit; an empty
 * one is « Non renseigné ». A retired document carries none (no description, no total).
 */
export function caracteristiquesDocument(document: DocumentDpePublic): Caracteristique[] {
	const servies = document.caracteristiques.map((valeur) => {
		const libelle = Object.hasOwn(LIBELLES_CARACTERISTIQUES, valeur.field_id) ? LIBELLES_CARACTERISTIQUES[valeur.field_id] : null;
		return caracteristique(document, valeur, libelle, etatDe(valeur, texteCaracteristique(valeur), null));
	});
	const reperes = document.reperes;
	const totaux = reperes
		? TOTAUX_ANNUELS.map(({ etiquette, libelle }) => {
				const total = reperes[etiquette].mesure;
				return caracteristique(document, total, libelle, etatDe(total, nombre(total)));
			})
		: [];
	return [...servies, ...totaux];
}

// --- « Adresse et rattachement » (UI-SPEC point 5, P-ADR-01) ----------------------------------

export const TITRE_ADRESSE_DECLAREE = 'Adresse déclarée dans le document';
export const TITRE_ADRESSE_BAN = 'Adresse de la BAN retenue par la source';
export const NATURE_RAPPROCHEMENT = 'clé BAN déclarée par la source, non vérifiée';
export const PHRASE_DIVERGENCE = `L'adresse déclarée diffère de l'adresse retenue${NNB}; les deux sont conservées.`;
/** A BAN key declared by the source and absent from the hosted BAN (owner, row 9). */
export const PHRASE_CLE_BAN_INCONNUE = 'Clé BAN déclarée par la source, introuvable dans la BAN importée';
/** A retained BAN key whose rows disagree, so no label is served (owner, row 10). */
export const LIBELLE_BAN_INDETERMINE = `Adresse de la BAN non déterminée${NB}: les sources ne s'accordent pas`;

export interface ModeleAdresse {
	declaree: { kind: 'publiee'; texte: string } | { kind: 'masquee'; texte: string } | { kind: 'etat'; etat: EtatAffiche };
	ban:
		| { kind: 'retenue'; href: string; libelle: string; nature: string }
		| { kind: 'etat'; etat: EtatAffiche }
		| { kind: 'texte'; texte: string }
		| { kind: 'provisoire' };
	/** The divergence sentence: only for a published and divergent declared address. */
	divergente: boolean;
}

function nonRenseigne(): EtatAffiche {
	const etat: EtatAffiche = { kind: 'absence', etat: 'not_reported', motif: '', effectif: null };
	const aide = phraseEtat('not_reported', '', null, 'document');
	if (aide) etat.aide = aide;
	return etat;
}

/**
 * The declared address as published by the API (never corrected, never replaced by the BAN), the
 * BAN address retained by the source with the nature of the match, and the divergence sentence.
 */
/**
 * The declared address as the page writes it: published between « », masked with the sentence of
 * its reason, else « Non renseigné » (the document carries none). Also the comparator's cell.
 */
export function adresseDeclaree(adresse: AdresseDeclaree | null): ModeleAdresse['declaree'] {
	if (adresse?.etat === 'publiee' && adresse.texte) return { kind: 'publiee', texte: `«${NNB}${adresse.texte}${NNB}»` };
	if (adresse?.etat === 'masquee') {
		const phrase =
			adresse.motif && Object.hasOwn(PHRASES_MOTIFS_ADRESSE_DECLAREE, adresse.motif) ? PHRASES_MOTIFS_ADRESSE_DECLAREE[adresse.motif] : null;
		return { kind: 'masquee', texte: phrase ? `non affichée${NB}: ${phrase}` : 'non affichée' };
	}
	// The document carries no address: an empty field of an existing document.
	return { kind: 'etat', etat: nonRenseigne() };
}

/**
 * The 13 px line of a document column of the comparator (UI-SPEC « Colonnes », 2026-10-02):
 * « Adresse déclarée : « {texte} » » when published, « Adresse déclarée non affichée » when masked
 * (its reason in the row « Adresse déclarée » and its panel); nothing when the document has none.
 */
export function ligneAdresseDeclaree(adresse: AdresseDeclaree | null): string | null {
	if (adresse?.etat === 'publiee' && adresse.texte) return `Adresse déclarée${NB}: «${NNB}${adresse.texte}${NNB}»`;
	if (adresse?.etat === 'masquee') return 'Adresse déclarée non affichée';
	return null;
}

export function modeleAdresse(document: DocumentDpePublic): ModeleAdresse {
	const declaree = adresseDeclaree(document.adresse_declaree);
	const ban = document.adresse_ban;
	const href = lienAdresse(document);
	let modeleBan: ModeleAdresse['ban'];
	if (href) modeleBan = { kind: 'retenue', href, libelle: ban?.libelle ?? LIBELLE_BAN_INDETERMINE, nature: NATURE_RAPPROCHEMENT };
	else if (ban?.etat === 'sans_cle') modeleBan = { kind: 'etat', etat: nonRenseigne() };
	else if (ban?.etat === 'cle_inconnue') modeleBan = { kind: 'texte', texte: PHRASE_CLE_BAN_INCONNUE };
	else if (ban?.etat === 'hors_couverture') {
		modeleBan = { kind: 'etat', etat: { kind: 'absence', etat: 'out_of_coverage', motif: ban.motif ?? '', effectif: null } };
	} else if (ban?.etat === 'indisponible') {
		modeleBan = { kind: 'etat', etat: { kind: 'absence', etat: 'temporary_error', motif: '', effectif: null } };
	}
	// A retained state without its key cannot be linked nor worded: the neutral label, provisional.
	else modeleBan = { kind: 'provisoire' };
	return { declaree, ban: modeleBan, divergente: declaree.kind === 'publiee' && document.adresse_divergente === true };
}

// --- « Provenance et validité » (UI-SPEC point 6) ---------------------------------------------

/** Contract V3 labels of the two ADEME datasets (CONTRAT-DPE-DOCUMENT.md, « Objet ajouté »). */
const LIBELLES_JEUX: Readonly<Record<string, string>> = {
	dpe03existant: 'DPE des logements existants',
	dpe02neuf: 'DPE des logements neufs'
};

/** The served datasets of the document, each with its catalogue page (https, served). */
export function jeuxSources(document: DocumentDpePublic): { libelle: string; url: string }[] {
	return document.source_identity.sources.map((source) => ({ libelle: LIBELLES_JEUX[source.dataset], url: source.url }));
}

export interface LigneStatut {
	parties: { texte: string; href?: string }[];
	provisoire: boolean;
	/** Sentence of the source panel of the status, when the owner gave one (retired document). */
	phrase?: string;
}

const PROVISOIRE: LigneStatut = { parties: [{ texte: LIBELLE_NON_AFFICHABLE }], provisoire: true };

/** One-line wording of the served codes without a served date or link (UI-SPEC point 6, owner rows 3 to 7). */
/** A document the source no longer publishes (owner, row 20): also a badge of the comparator. */
export const LIBELLE_RETIRE = "Plus publié par l'ADEME";

const LIBELLES_STATUT: Readonly<Record<string, { texte: string; phrase?: string }>> = {
	en_vigueur: { texte: 'En vigueur' },
	remplacement_non_verifie: { texte: LIBELLES_DOCUMENT_DPE.remplacement_incertain.libelle },
	ancienne_methode: { texte: "Établi selon l'ancienne méthode (avant juillet 2021)" },
	validite_inconnue: { texte: 'Validité non déterminée' },
	dates_incoherentes: { texte: `Dates incohérentes${NB}: validité non déterminée` },
	etablissement_futur: { texte: `Date d'établissement postérieure à aujourd'hui${NB}: validité non déterminée` },
	retire: {
		texte: LIBELLE_RETIRE,
		phrase: `La source ne publie plus ce document${NNB}; DataCommune conserve son numéro et son statut.`
	}
};

/**
 * The served status in one line (UI-SPEC point 6 and owner's wording of 2026-10-05): « Expiré le
 * {date} » (« Expiré » when no date is served), « Remplacé par le DPE n° {numéro} selon l'ADEME »,
 * « Remplacé selon l'ADEME » when no successor number is served (row 24, as on the card), then the
 * closed table above. A code served later is the neutral label, marked provisional. No date is
 * computed.
 */
export function ligneStatut(document: DocumentDpePublic): LigneStatut {
	const statut = document.statut;
	if (statut.code === 'expire') {
		const date = dateLongue(statut.date);
		return { parties: [{ texte: date ? `Expiré le ${date}` : LIBELLES_DOCUMENT_DPE.expire.libelle }], provisoire: false };
	}
	if (statut.code === 'remplace' && statut.remplace_par) {
		return {
			parties: [
				{ texte: 'Remplacé par le ' },
				{ texte: titreDocument(statut.remplace_par), href: lienDocument(statut.remplace_par) },
				{ texte: " selon l'ADEME" }
			],
			provisoire: false
		};
	}
	if (statut.code === 'remplace') {
		return { parties: [{ texte: LIBELLES_DOCUMENT_DPE.remplace.libelle }], provisoire: false };
	}
	if (Object.hasOwn(LIBELLES_STATUT, statut.code)) {
		const { texte, phrase } = LIBELLES_STATUT[statut.code];
		return phrase ? { parties: [{ texte }], provisoire: false, phrase } : { parties: [{ texte }], provisoire: false };
	}
	return PROVISOIRE;
}

/** The document this one declares it replaces, as a link to its page (served number). */
export function documentRemplace(document: DocumentDpePublic): { texte: string; href: string } | null {
	const numero = document.statut.remplace;
	return numero ? { texte: titreDocument(numero), href: lienDocument(numero) } : null;
}

// --- States of the page (UI-SPEC « Contrat de rédaction ») -------------------------------------

export const TITRE_PANNE_DOCUMENT = 'Ce document est momentanément indisponible';
/** The outage sentence of phase 3 (commune fiche, comparator, address page). */
export const MESSAGE_PANNE_DOCUMENT = MESSAGE_PANNE_ADRESSE;
export const CHARGEMENT_DOCUMENT = 'Chargement du document…';

const NOMS_DEPARTEMENTS = new Map(DEPARTEMENTS.map((departement) => [departement.code, departement.nom]));

function listeFrancaise(elements: string[]): string {
	return elements.length < 2 ? elements.join('') : `${elements.slice(0, -1).join(', ')} et ${elements.at(-1)}`;
}

/** « DPE inconnu » when the instance hosts no DPE at all (owner, row 11). */
export const PHRASE_AUCUN_DPE_IMPORTE = "Aucun document DPE n'est importé par ce site.";

/** Above this many served departements the sentence gives their number, not their names (owner, 2026-11-09). */
const DEPARTEMENTS_NOMMES_AU_PLUS = 5;

/**
 * « DPE inconnu » (404): the served departements of the instance, never the input; without any
 * served departement (the DPE is not hosted), the owner's sentence. Named up to five; beyond, counted:
 * with the whole country served the sentence listed 101 names.
 */
export function phraseInconnu(departements: string[]): SegmentIdentite {
	if (!departements.length) return { texte: PHRASE_AUCUN_DPE_IMPORTE };
	if (departements.length > DEPARTEMENTS_NOMMES_AU_PLUS) {
		return {
			texte: `Aucun document DPE ne porte ce numéro dans la base importée par ce site (${departements.length}${NB}départements). Cela ne veut pas dire qu'il n'existe pas.`
		};
	}
	const couverture = listeFrancaise(
		departements.map((code) => libelleDepartement({ code, nom: NOMS_DEPARTEMENTS.get(code) ?? null }))
	);
	return {
		texte: `Aucun document DPE ne porte ce numéro dans la base importée par ce site, qui couvre ${couverture}. Cela ne veut pas dire qu'il n'existe pas.`
	};
}

/** h1 of a malformed number (owner, row 14): the input is quoted in the sentence only. */
export const TITRE_NUMERO_NON_VALIDE = 'Numéro DPE non valide';
/** Constant message of the 404 of an unknown number, read by `+error.svelte` (row 13). */
export const ERREUR_DPE_INCONNU = 'DPE inconnu';

/** « « {saisie} » n'a pas la forme d'un numéro DPE de l'ADEME. » */
/** Longest part of a malformed input quoted back (IN-05 of the D-15 review). */
export const CITATION_MAX = 40;

export function phraseMalForme(saisie: string): string {
	// A forged URL never makes the site write a long arbitrary text: 40 characters, then « … ».
	const citee = [...saisie].length > CITATION_MAX ? `${[...saisie].slice(0, CITATION_MAX).join('')}…` : saisie;
	return `«${NNB}${citee}${NNB}» n'a pas la forme d'un numéro DPE de l'ADEME.`;
}
