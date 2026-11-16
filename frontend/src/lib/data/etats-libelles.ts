// Single glossary of the served states (UI-SPEC « Les quatre états servis », glossary table,
// repeated on `/datasets`). Pure module — zero Svelte imports, leaf module.
//
// ZERO, MISSING, OUT OF COVERAGE, OUTAGE are four states and are worded apart: a missing value
// carries the label of its served sub-state (D-47), out of coverage the label of its reason
// (D-24), an outage « Indisponible ». The technical key is never displayed.
//
// TWO VOCABULARIES, deliberately kept apart during the redesign:
//   - `LIBELLES_ETATS` / `PHRASES_ETATS` / `LIBELLES_MOTIFS`: the UI-SPEC glossary, read by
//     `StateValue` and every redesigned screen;
//   - `LIBELLES_ECARTS_HISTORIQUES` / `EXPLICATIONS_ETATS`: the wording of the screens not yet
//     redesigned (themes of the commune fiche until 03-08, Fiche Bien until phase 4). Their
//     mappers use `out_of_coverage` for « dataset empty or not requested » too (`formatField`,
//     `bien-fiche.ts`): worded « Hors couverture » there, a cell would say the instance does not
//     cover a place it does cover. They keep their wording until they move to `StateValue`.

import type { FieldValue, OutOfCoverageReason } from '$lib/types/fiche';

/** Served absence state → label of the UI-SPEC glossary. A served value needs no label. */
export const LIBELLES_ETATS: Record<string, string> = {
	not_reported: 'Non renseigné',
	insufficient_sample: 'Effectif insuffisant',
	suppressed: 'Secret statistique',
	not_available: 'Non publiée',
	zero_denominator: 'Sans base de calcul',
	not_applicable: 'Sans objet',
	not_qualified: 'Non qualifié',
	/** Worded by its reason when it is one of the three contract reasons (`libelleEtat`). */
	out_of_coverage: 'Hors couverture',
	temporary_error: 'Indisponible'
};

/**
 * Served state → sentence of the UI-SPEC glossary, shown as help next to the badge. `{n}` is the
 * served size (`phraseEtat` writes it, or drops it when it is not served). `value` has no entry: a
 * displayed figure explains itself. Out of coverage is worded by its reason (`LIBELLES_MOTIFS`).
 */
export const PHRASES_ETATS: Record<string, string> = {
	not_reported: 'La source ne fournit pas cette valeur pour cette commune.',
	insufficient_sample: "Trop peu d'observations ({n}) pour publier une valeur.",
	suppressed: 'Le producteur ne publie pas cette valeur pour protéger les personnes.',
	not_available: 'La source ne publie pas cette valeur pour ce territoire.',
	zero_denominator: 'Aucun élément ne permet de calculer ce taux.',
	not_applicable: "Cette information ne s'applique pas ici.",
	not_qualified: 'Ce jeu est en quarantaine : ses valeurs ne sont pas présentées comme fiables.',
	temporary_error: "Le service n'a pas répondu. Réessayez dans un instant."
};

/**
 * Reasons of « hors couverture » (D-24), worded as the UI-SPEC glossary. A reason qualifies the
 * out-of-coverage state; it is never a fifth state and never reads as zero.
 */
export const LIBELLES_MOTIFS: Record<OutOfCoverageReason, { libelle: string; phrase: string }> = {
	dataset_non_heberge: {
		libelle: 'Hors couverture · jeu non installé',
		phrase: 'Ce site ne propose pas ce jeu de données.'
	},
	territoire_hors_perimetre: {
		libelle: 'Hors couverture · département non hébergé',
		phrase: 'Ce site ne couvre pas ce département.'
	},
	source_sans_couverture: {
		libelle: 'Hors couverture · non couvert par la source',
		phrase: 'La source ne couvre pas ce territoire.'
	}
};

/**
 * Quarantine worded by its served reason (03-17 F55, SEM-01): a quarantined indicator is not
 * imputed to its whole dataset. A reason outside this table keeps the generic sentence of
 * `PHRASES_ETATS.not_qualified` (also read by `/datasets`).
 */
export const PHRASES_MOTIFS_NOT_QUALIFIED: Record<string, string> = {
	indicator_policy_unqualified: "Cet indicateur n'est pas encore publié : sa règle de calcul n'a pas été validée.",
	territorial_attribution_unqualified:
		"Cet indicateur n'est pas publié : son rattachement à la commune n'est pas vérifié.",
	/** 04.1-09, UI-032: the commune is outside the qualified build; no dataset is in quarantine. */
	commune_hors_build_qualifie: 'Ces indicateurs ne sont pas encore qualifiés pour cette commune.'
};

/**
 * Served reasons of an absence, worded for the source panel (03-17 F57, F58). A technical key is
 * never displayed: `libelleMotifAbsence` returns the entry of this table, the reason itself when it
 * is already a sentence, or null.
 */
export const LIBELLES_MOTIFS_ABSENCE: Record<string, string> = {
	indicator_policy_unqualified: 'Indicateur non encore validé',
	territorial_attribution_unqualified: 'Rattachement à la commune non vérifié',
	empty_sample: 'Aucune vente retenue sur la période',
	known_class_denominator_zero: 'Aucun diagnostic avec une classe connue',
	commune_hors_build_qualifie: 'Indicateurs non qualifiés pour cette commune'
};

/** Label of a served reason, or null when it is a technical key outside the table. */
export function libelleMotifAbsence(motif: string | null | undefined): string | null {
	if (!motif) return null;
	if (Object.hasOwn(LIBELLES_MOTIFS_ABSENCE, motif)) return LIBELLES_MOTIFS_ABSENCE[motif];
	return motif.includes(' ') && !motif.includes('_') ? motif : null;
}

/**
 * Reasons of the parcel relation of an address (UI-SPEC « Contrat de rédaction », amended on
 * 2026-10-02) and of its position (landmark ③, 2026-10-05): a point outside every parcel, several
 * parcels, an undetermined parcel or position are states of their own, read by reason BEFORE the
 * generic label of their served state (never « Non publiée »). `{n}` is the served number of
 * parcels; without it the sentence is not written.
 */
export const LIBELLES_MOTIFS_PARCELLE: Record<string, { libelle: string; phrase: string }> = {
	point_hors_parcelle: {
		libelle: 'Point hors parcelle',
		phrase: "Le point de l'adresse ne tombe dans aucune parcelle du cadastre importé : aucune vente n'y est rattachée."
	},
	parcelles_multiples: {
		libelle: 'Plusieurs parcelles',
		phrase: "Le point de l'adresse touche {n} parcelles ; aucune n'est choisie."
	},
	/** Landmark ② (04-DECISIONS-TEXTES, 2026-10-05, row 5): no single parcel is designated. */
	parcelle_indeterminee: {
		libelle: 'Parcelle non déterminée',
		phrase: "Le point de l'adresse ne désigne pas une seule parcelle."
	},
	/** Landmark ③ (same decisions, row 6): the position is not established, no exposure is computed. */
	position_indeterminee: {
		libelle: 'Position non déterminée',
		phrase: "La position de l'adresse n'est pas établie ; l'exposition au point n'est pas calculée."
	}
};

/**
 * Sentences of served reasons of a value that is not published (`not_available`), decided by the
 * owner (04-DECISIONS-TEXTES rows 44, 45, 49 to 51, 56; row 75 validated as is, row 77). Read before the generic sentence of the
 * state; the label stays the state's (« Non publiée »).
 */
export const PHRASES_MOTIFS_NON_PUBLIES: Record<string, string> = {
	/** Row 44: fibre, the Arcep address does not exactly match the BAN one. */
	adresse_non_verifiable: "L'adresse publiée par l'Arcep ne correspond pas exactement à celle de la BAN ; l'éligibilité n'est pas affichée.",
	/** Row 45: town planning, no document published on the GPU for the commune. */
	aucun_document_publie: "Aucun document d'urbanisme n'est publié sur le GPU pour cette commune.",
	/** Row 49: several buildings cover the point of the address. */
	batiment_du_point_non_unique: "Plusieurs bâtiments couvrent le point de l'adresse ; aucun n'est retenu.",
	/** Row 75, validated as is (row 77, C54; C52, B3): the RNB links several buildings to the address. */
	batiment_rnb_non_unique: "Le RNB rattache plusieurs bâtiments à cette adresse ; aucun n'est retenu.",
	/** Row 50, reworded on 2026-11-11 as the report's verdict: only the ADEME dataset opened in July 2021 is imported. */
	aucun_document: 'Aucun DPE depuis juillet 2021 à cette adresse ; les diagnostics plus anciens ne sont plus valables.',
	/** Row 51: the most recent document, no dated document. */
	aucun_document_date: "Aucun DPE daté à cette adresse ; le plus récent ne peut pas être déterminé.",
	/** Row 56 (replaces row 52): more than CANDIDATS_MAX documents, the selection is not computed. */
	population_incomplete: "Plus de 1 000 documents sont déclarés à cette adresse ; le plus récent n'est pas déterminé."
};

function motifParcelle(motif: string): { libelle: string; phrase: string } | null {
	return Object.hasOwn(LIBELLES_MOTIFS_PARCELLE, motif) ? LIBELLES_MOTIFS_PARCELLE[motif] : null;
}

/**
 * Labels of a DPE document (UI-SPEC « Glossaire — deltas », « Politique d'affichage DPE »): facts
 * on a document, never a data state and never a `warn`. « Expiré », « Ancienne méthode » and the
 * two date anomalies carry their sentence for the source panel. `remplace` names no successor: its
 * number is not served on a card and stays on `/dpe/[numero]` (04-DECISIONS-TEXTES, 2026-10-04/05).
 * The former `perime` state is retired (C34, 04-12): a document past its validity is « Expiré ».
 */
export const LIBELLES_DOCUMENT_DPE: Record<string, { libelle: string; phrase: string | null }> = {
	plus_recent: { libelle: 'Le plus récent', phrase: null },
	meme_date_plus_recente: { libelle: 'Même date la plus récente', phrase: null },
	ancienne_methode: {
		libelle: 'Ancienne méthode',
		phrase:
			"Établi avant juillet 2021 avec l'ancienne méthode de calcul ; ses classes ne se comparent pas directement à celles des DPE récents."
	},
	expire: {
		libelle: 'Expiré',
		phrase: 'Ce document a dépassé sa durée de validité de 10 ans. Il reste listé avec sa date.'
	},
	/** Status wording of the document page (UI-SPEC « /dpe/[numero] » point 6). */
	remplacement_incertain: { libelle: 'Remplacement non vérifié', phrase: null },
	remplace: { libelle: "Remplacé selon l'ADEME", phrase: null },
	dates_incoherentes: {
		libelle: 'Dates incohérentes',
		phrase: 'Les dates déclarées dans ce document ne sont pas cohérentes entre elles ; sa validité ne peut pas être établie.'
	},
	etablissement_futur: {
		libelle: "Date d'établissement future",
		phrase: "La date d'établissement déclarée est postérieure à aujourd'hui ; sa validité ne peut pas être établie."
	}
};

/**
 * Reasons of a masked declared address (UI-SPEC « Contrat de rédaction », « Adresse déclarée non
 * affichée », P-ADR-01 decided on 2026-10-02): the end of the sentence « Adresse déclarée non
 * affichée : {phrase} ». Never the generic label of `suppressed` (« Secret statistique »).
 */
export const PHRASES_MOTIFS_ADRESSE_DECLAREE: Record<string, string> = {
	complement_present: 'le document y joint un complément (étage, porte, bâtiment ou nom).',
	non_verifiable_sans_complement: 'elle ne se vérifie pas comme une adresse sans complément.'
};

/** Source panel sentence of a masked declared address (same UI-SPEC row). */
export const PHRASE_CONSERVATION_ADRESSE_DECLAREE =
	"Le texte saisi par le diagnostiqueur est conservé par DataCommune et n'est pas publié.";

/** Label of a served value the API sent without a usable state (03-17 C6, glossary). */
export const LIBELLE_NON_AFFICHABLE = 'Valeur non affichable';

/** Name read by the search results (03-04): the same table, one wording per reason. */
export const MOTIFS_HORS_COUVERTURE = LIBELLES_MOTIFS;

/** Badge of a place out of coverage whose reason is not one of the three contract reasons. */
export const LIBELLE_HORS_COUVERTURE = LIBELLES_ETATS.out_of_coverage;

function motifContrat(motif: string): OutOfCoverageReason | null {
	return Object.hasOwn(LIBELLES_MOTIFS, motif) ? (motif as OutOfCoverageReason) : null;
}

/** Label of one served absence: its sub-state, or the reason of an out-of-coverage state. */
export function libelleEtat(etat: string, motif: string): string {
	const parcelle = motifParcelle(motif);
	if (parcelle) return parcelle.libelle;
	if (etat === 'out_of_coverage') {
		const cle = motifContrat(motif);
		return cle ? LIBELLES_MOTIFS[cle].libelle : LIBELLE_HORS_COUVERTURE;
	}
	return LIBELLES_ETATS[etat] ?? '';
}

const entier = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

/**
 * Object a « not reported » sentence speaks of (UI-SPEC « Glossaire — deltas », 04-18): the commune
 * by default (every existing caller), the address or the document on the new pages.
 */
export type ObjetPhrase = 'commune' | 'adresse' | 'document';

const FINS_NOT_REPORTED: Record<ObjetPhrase, string> = {
	commune: 'pour cette commune.',
	adresse: 'pour cette adresse.',
	document: 'dans ce document.'
};

/** Help sentence of one served absence, `null` when the glossary has none for it. */
export function phraseEtat(
	etat: string,
	motif: string,
	effectif: number | null,
	objet: ObjetPhrase = 'commune'
): string | null {
	if (etat === 'not_reported') return `La source ne fournit pas cette valeur ${FINS_NOT_REPORTED[objet]}`;
	const parcelle = motifParcelle(motif);
	if (parcelle) {
		if (!parcelle.phrase.includes('{n}')) return parcelle.phrase;
		return effectif == null ? null : parcelle.phrase.replace('{n}', entier.format(effectif));
	}
	if (etat === 'out_of_coverage') {
		const cle = motifContrat(motif);
		return cle ? LIBELLES_MOTIFS[cle].phrase : null;
	}
	if (etat === 'not_available' && Object.hasOwn(PHRASES_MOTIFS_NON_PUBLIES, motif)) {
		return PHRASES_MOTIFS_NON_PUBLIES[motif];
	}
	if (etat === 'not_qualified' && Object.hasOwn(PHRASES_MOTIFS_NOT_QUALIFIED, motif)) {
		return PHRASES_MOTIFS_NOT_QUALIFIED[motif];
	}
	const phrase = PHRASES_ETATS[etat];
	if (!phrase) return null;
	return effectif == null
		? phrase.replace(' ({n})', '')
		: phrase.replace('{n}', entier.format(effectif));
}

/**
 * Historical wording of the D-04 states of `formatField` (Phase 49, D-10), still rendered by the
 * themes of the commune fiche (until 03-08) and by the Fiche Bien (until phase 4). Lower-case
 * entries read as the continuation of a row (« Loyer médian : non disponible »).
 */
export const LIBELLES_ECARTS_HISTORIQUES: Record<FieldValue['state'], string> = {
	value: 'Données présentes',
	not_reported: 'Non renseigné',
	out_of_coverage: 'non disponible',
	temporary_error: 'non disponible',
	not_applicable: 'non applicable'
};

/**
 * Sentences of the former ℹ️ panels of the same screens (Phase 49), kept byte-identical until
 * they are redesigned.
 */
export const EXPLICATIONS_ETATS: Record<string, string> = {
	not_reported: "La source publique n'a pas renseigné cette information pour ce point précis.",
	out_of_coverage:
		"Cette donnée n'est pas couverte ici (hors périmètre de la démonstration ou non collectée pour cette zone).",
	temporary_error:
		'Donnée temporairement indisponible — nouvelle tentative au prochain rafraîchissement.',
	not_applicable: "Cette information ne s'applique pas à ce bien ou à cette commune."
};

/**
 * Paris, Lyon, Marseille (R-09): a value absent here that the source publishes at the other mesh. The API
 * says which one (`etats[colonne].publiee_a`); the row keeps its state (« Non renseigné ») and says where the
 * value is, never a sum of the arrondissements nor a statistic of the whole commune. Wording validated by
 * Ianis (2026-11-10). `{ville}`: the name of the commune.
 */
export const PHRASES_PUBLIEE_AILLEURS: Readonly<Record<'arrondissement' | 'commune', string>> = {
	arrondissement: 'Publié par arrondissement : voir les arrondissements de {ville}.',
	commune: 'Publié pour la commune entière : voir la fiche de {ville}.'
};

/** The sentence of a value published at the other mesh, with the name of its commune. */
export function phrasePublieeAilleurs(maille: 'arrondissement' | 'commune', ville: string): string {
	return PHRASES_PUBLIEE_AILLEURS[maille].replace('{ville}', ville);
}
