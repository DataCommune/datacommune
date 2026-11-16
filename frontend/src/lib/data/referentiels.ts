// Published national reference values for the Fiche (Phase 49, FICHE-04). Pure module — zero
// Svelte imports, zero network access, LEAF module: the mappers import this, never the reverse
// (a cycle would make the referential depend on the very thing it is meant to judge).
//
// WHY THIS MODULE EXISTS (D-01, documentary amendment to the Phase 8 D-01 decision):
// `VERDICT_THRESHOLDS` (Commune mapper) and `BIEN_VERDICT_THRESHOLDS` (Bien mapper) encode
// in-house cut-offs ("> 8 %", ">= 22 000 €") that no published source backs. They colour a chip
// without ever telling the reader *against what* the value was judged. This module replaces that
// justification surface with PUBLISHED national referentials: each entry carries its value, its
// source, its millésime and its publication URL, so every positioning shown in the UI can be
// traced back to a document the reader can open.
//
// D-02 — NEVER compute a national aggregate from our own database. The production instance only
// covers départements 44 and 85: an average computed there is a regional artefact, not a national
// reference. Every constant below comes from an official publication, verbatim.
//
// D-04 — the 4-variant palette (good/neutral/watch/info) is RE-SEMANTICISED as a POSITION
// relative to the referential, never as an editorial opinion on the commune or the property:
//   - good    : above / on the favourable side of the published reference
//   - neutral : close to the reference, inside the documented tolerance band
//   - watch   : below / on the side the reference itself flags (e.g. passoire énergétique)
//   - info    : contextual, no judgement carried
// There is NO 5th variant. A metric with no comparable published referential gets no chip at all
// (D-07, `SANS_REFERENTIEL` below) — honest silence beats an approximate comparison.
//
// D-06 — the "close to" tolerance band lives in the entry itself (`tolerancePct`), never as a
// hidden constant inside the comparison function: the ℹ️ can state the band it applied.
//
// D-12 — acronyms expanded for the explanation dictionary (Wave 3): DPE = diagnostic de
// performance énergétique · DVF = demandes de valeurs foncières · INSEE = Institut national de la
// statistique et des études économiques · Filosofi = fichier localisé social et fiscal ·
// LOVAC = logements vacants (fichier issu des données fiscales) · ADEME = Agence de la transition
// écologique · EPCI = établissement public de coopération intercommunale.
//
// SOURCE VERIFICATION (T-49-01, 2026-07-29 — see 49-01-SUMMARY.md for the full log): every URL
// below was fetched live (HTTP 200) at execution time. A value/URL that cannot be confirmed live
// falls back to the couple frozen in 49-01-PLAN.md — never to an invented URL.

import { formatBareNumber } from './formatters';

/** Field ids are the SAME key space as `fiche-fields.v1.json` — never ad hoc keys. */
export interface ReferentielEntry {
	/** Human-readable publication name, rendered as-is in the ℹ️. */
	source: string;
	/** Vintage of the published reference (year or arrêté year). */
	millesime: string;
	/** Publication URL — https only, checked by the structure test (D-03). */
	url: string;
	/** D-06 — visible "close to" band, in percent. Numeric referentials only. */
	tolerancePct?: number;
}

/** A numeric national reference (a published figure the field value is compared against). */
export interface ReferentielNumerique extends ReferentielEntry {
	kind: 'numerique';
	valeur: number;
	/** Plain-language name of the compared concept, used in the ℹ️ sentence. */
	libelleValeur: string;
	tolerancePct: number;
}

/**
 * D-16 — DECLARED gradation of a categorical referential. A classification is only allowed to
 * COLOUR its classes when the publication that establishes them also ORDERS them; `fondement` is
 * the fragment of the entry's own `explication` that establishes that order, so the justification
 * the code relies on is the very sentence the reader receives in the ℹ️ (`ecartTexte`).
 */
export interface ReferentielGradation {
	/** Verbatim substring of the entry's `explication` that publishes the order. */
	fondement: string;
}

interface ReferentielCategorielBase extends ReferentielEntry {
	kind: 'categoriel';
	/** Sentence for the ℹ️ — cites the referential and its millésime. */
	explication: string;
}

/**
 * A published classification WITHOUT a published order (A-01). The class is rendered as-is; no
 * class may carry anything but `info` — the type, not a convention, forbids it (fail-closed D-16,
 * same discipline as the ℹ️ dictionary of 49-02).
 */
export interface ReferentielCategorielDescriptif extends ReferentielCategorielBase {
	gradation: null;
	/** Normalised class key → label template ({valeur} = the raw value as received). */
	labels: Record<string, { verdict: 'info'; label: string }>;
}

/** A published classification whose source ORDERS its classes, hence may position the value. */
export interface ReferentielCategorielGradue extends ReferentielCategorielBase {
	gradation: ReferentielGradation;
	/** Normalised class key → verdict + label template ({valeur} = the raw value as received). */
	labels: Record<string, { verdict: Comparaison['verdict']; label: string }>;
}

/** A categorical national reference (an official classification the value belongs to). */
export type ReferentielCategoriel = ReferentielCategorielDescriptif | ReferentielCategorielGradue;

export interface Comparaison {
	verdict: 'good' | 'neutral' | 'watch' | 'info';
	/** Short verbal chip label (D-05) — e.g. "Au-dessus de la médiane". */
	label: string;
	/** Full sentence for the ℹ️ (numeric gap + millésime + source). */
	ecartTexte: string | null;
}

/**
 * Published national referentials, keyed by `field_id` (fiche-fields.v1.json key space).
 *
 * 1. `revenus.revenu_median` uses FILO2021_DEC_COM Q221: declared income per
 *    consumption unit. The reference is Q221 of FILO2021_DEC_METROPOLE (22900),
 *    not DISP (23080). Comparison requires the same vintage and metropolitan scope.
 * 2. `zonage_abc.zone` — the referential is the official A/B/C classification itself, rendered AS
 *    PUBLISHED and never graded (`gradation: null`): the arrêté classifies market TENSION and
 *    publishes no order from favourable to unfavourable, so every class carries `verdict: 'info'`
 *    and the reader gets the zone plus its explanation, without a colour deciding for them
 *    (A-01, 49-VERIFICATION §1.b).
 * 3. `bien.dpe.etiquette_dpe` — the referential is the official DPE scale in force since the
 *    1 July 2021 reform; "passoire énergétique" is the legal definition (classes F and G), not an
 *    editorial judgement.
 */
export const REFERENTIELS: Record<string, ReferentielNumerique | ReferentielCategoriel> = {
	'revenus.revenu_median': {
		kind: 'numerique',
		valeur: 22900,
		libelleValeur: 'revenu déclaré médian annuel par unité de consommation',
		source: 'INSEE — Filosofi 2021, revenus déclarés, France métropolitaine (DEC_METROPOLE, Q221)',
		millesime: '2021',
		url: 'https://www.insee.fr/fr/statistiques/7756855',
		tolerancePct: 5
	},
	'zonage_abc.zone': {
		kind: 'categoriel',
		// A-01 — the arrêté publishes a TENSION classification, never an order of desirability: a
		// tight market favours the seller, not the buyer reading this fiche. No published order, no
		// gradation, hence no colour that decides for the reader.
		gradation: null,
		labels: {
			ABIS: { verdict: 'info', label: 'Marché tendu (zone {valeur})' },
			A: { verdict: 'info', label: 'Marché tendu (zone {valeur})' },
			B1: { verdict: 'info', label: 'Marché tendu (zone B1)' },
			B2: { verdict: 'info', label: 'Marché intermédiaire (zone B2)' },
			C: { verdict: 'info', label: 'Marché détendu (zone C)' }
		},
		source: 'Zonage A/B/C — Ministère chargé du logement',
		millesime: '2026',
		url: 'https://www.ecologie.gouv.fr/politiques-publiques/zonage-b-c',
		explication:
			'Classement officiel des communes en zones A bis, A, B1, B2 et C selon le déséquilibre entre offre et demande de logements (Ministère chargé du logement, dernière révision : arrêté du 23 juin 2026).'
	},
	'bien.dpe.etiquette_dpe': {
		kind: 'categoriel',
		// The order IS published: the loi Climat et Résilience names F and G as the unfavourable
		// end of the scale. The fragment below is a literal substring of `explication`, so the
		// justification the gradation rests on is the one the reader receives in the ℹ️.
		gradation: {
			fondement:
				'les classes F et G sont les « passoires énergétiques » au sens de la loi Climat et Résilience'
		},
		labels: {
			A: { verdict: 'good', label: 'Bonne performance (classe {valeur})' },
			B: { verdict: 'good', label: 'Bonne performance (classe {valeur})' },
			C: { verdict: 'good', label: 'Bonne performance (classe {valeur})' },
			D: { verdict: 'neutral', label: 'Performance moyenne (classe {valeur})' },
			E: { verdict: 'neutral', label: 'Performance moyenne (classe {valeur})' },
			F: { verdict: 'watch', label: 'Passoire énergétique (classe {valeur})' },
			G: { verdict: 'watch', label: 'Passoire énergétique (classe {valeur})' }
		},
		source: 'Échelle DPE officielle — Ministère de la Transition écologique / ADEME',
		millesime: '2021',
		url: 'https://www.ecologie.gouv.fr/politiques-publiques/diagnostic-performance-energetique-dpe',
		explication:
			"Échelle officielle du diagnostic de performance énergétique, de A à G, en vigueur depuis la réforme du 1er juillet 2021 ; les classes F et G sont les « passoires énergétiques » au sens de la loi Climat et Résilience."
	}
};

/**
 * D-07 — metrics for which NO published national referential is directly comparable. They get no
 * chip at all; the reason below is rendered verbatim in the ℹ️ so the absence of comparison is
 * itself an explicit, sourced statement rather than an unexplained blank.
 */
export const SANS_REFERENTIEL: Record<string, string> = {
	'vacance.taux_vacance':
		"Aucun référentiel national publié n'est directement comparable : le taux national LOVAC le plus diffusé (4,1 %) ne mesure que la vacance structurelle (plus de 2 ans), pas le taux global affiché ici.",
	'loyers.loyer_m2_appartement':
		'Aucun référentiel national publié ne permet une comparaison fiable des loyers au m².',
	'loyers.loyer_m2_maison':
		'Aucun référentiel national publié ne permet une comparaison fiable des loyers au m².',
	'bien.dvf.prix_m2':
		"Un prix de vente passé n'est pas une estimation : aucune comparaison n'est affichée.",
	'bien.dvf.valeur_fonciere':
		"Un prix de vente passé n'est pas une estimation : aucune comparaison n'est affichée."
};

const FR_LOCALE = 'fr-FR';

/** fr-FR percentage gap, 1 decimal max — no parallel formatter, plain Intl (D-18). */
function formatPct(pct: number): string {
	return new Intl.NumberFormat(FR_LOCALE, { maximumFractionDigits: 1 }).format(Math.abs(pct));
}

/**
 * Class lookup key: uppercase, spaces removed — so the source spelling "A bis" and the compact
 * "Abis" reach the same entry. The RAW value is what gets interpolated into the label, never this
 * normalised form (we display what the source published, we only normalise to find it).
 */
function cleKey(valeur: string): string {
	return valeur.trim().toUpperCase().replace(/\s+/g, '');
}

function coerceNombre(valeur: string | number): number | null {
	const n = typeof valeur === 'number' ? valeur : Number(valeur);
	return Number.isFinite(n) ? n : null;
}

function comparerNumerique(
	fieldId: string,
	entree: ReferentielNumerique,
	valeur: string | number
): Comparaison | null {
	const n = coerceNombre(valeur);
	if (n == null) return null;

	const ecartPct = ((n - entree.valeur) / entree.valeur) * 100;
	const reference = formatBareNumber(fieldId, entree.valeur);
	const observee = formatBareNumber(fieldId, n);
	const prefixe = `Revenu déclaré médian — France métropolitaine ${entree.millesime} : ${reference} € — cette commune est à ${observee} €`;

	if (Math.abs(ecartPct) <= entree.tolerancePct) {
		return {
			verdict: 'neutral',
			label: 'Proche de la médiane',
			ecartTexte: `${prefixe}, soit un écart de ${formatPct(ecartPct)} % (tolérance « proche » : ±${entree.tolerancePct} %). Source : ${entree.source}.`
		};
	}

	const auDessus = ecartPct > 0;
	return {
		verdict: auDessus ? 'good' : 'watch',
		label: auDessus ? 'Au-dessus de la médiane' : 'En dessous de la médiane',
		ecartTexte: `${prefixe}, soit ${formatPct(ecartPct)} % ${auDessus ? 'au-dessus' : 'en dessous'}. Source : ${entree.source}.`
	};
}

function comparerCategoriel(
	entree: ReferentielCategoriel,
	valeur: string | number
): Comparaison | null {
	const brut = String(valeur).trim();
	if (brut === '') return null;

	const classe = entree.labels[cleKey(brut)];
	// An unknown class is NOT an error and NOT an `info` chip: the referential simply says nothing
	// about it, so nothing is shown (same treatment as a null value).
	if (classe == null) return null;

	return {
		verdict: classe.verdict,
		label: classe.label.replace('{valeur}', brut),
		ecartTexte: `${entree.explication} Source : ${entree.source} (${entree.millesime}).`
	};
}

/**
 * Compare a Fiche value against its published national referential.
 *
 * Returns `null` — i.e. NO chip, no judgement — whenever a comparison would be dishonest:
 *   - `valeur == null` (the Phase 43 invariant "value == null → no verdict" survives this rewrite);
 *   - the field is registered in `SANS_REFERENTIEL` (D-07 deliberate silence);
 *   - the field has no referential at all (uncompared metrics are the NORM, not an error);
 *   - the value cannot be read as the referential's type (unparsable number, unknown class).
 */
export function comparerAuReferentiel(
	fieldId: string,
	valeur: string | number | null,
	contexte?: { annee: number | null; codeInsee: string }
): Comparaison | null {
	if (valeur == null) return null;
	// DEC metropolitan 2021 is not a reference for another vintage or overseas.
	if (fieldId === 'revenus.revenu_median' &&
		(contexte?.annee !== 2021 || !/^(?:0[1-9]|[1-8][0-9]|9[0-5]|2[AB])[0-9]{3}$/.test(contexte.codeInsee))) return null;
	if (fieldId in SANS_REFERENTIEL) return null;

	const entree = REFERENTIELS[fieldId];
	if (entree == null) return null;

	return entree.kind === 'numerique'
		? comparerNumerique(fieldId, entree, valeur)
		: comparerCategoriel(entree, valeur);
}
