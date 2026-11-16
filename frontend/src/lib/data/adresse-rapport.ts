// Reading model of the address fiche on the report format (04.7-03, UI-SPEC v3). Pure module, zero
// Svelte imports. Since 04.7-17 the API serves the headline, the alert, the attachment sentence, the
// tone and the summary: they are read as served, never computed again here. What is left is wording
// (badges, scales, figures, details) of what each theme serves. Nothing is invented: a theme the
// front does not know renders from its own title, verdict, figures, scale, attachment and sources.

import type {
	ChiffreRapport,
	EtatTheme,
	GranulariteRapport,
	ResumeServi,
	SectionRapport,
	ThemeRapport
} from '$lib/api/rapport';
import { ETATS_SANS_REPONSE } from '$lib/api/rapport';
import { perimetreSecteur } from '$lib/data/echelles';
import { DRAPEAUX_MUETS_SI_FAUX, RESERVES_LECTURE, raisonReserve } from '$lib/data/explications-simples';
import { LIBELLES_DETAILS, MOTS_LISIBLES, VALEURS_LIBELLEES } from '$lib/data/libelles-details';
import { distanceFr, nombreFr } from '$lib/utils/nombre-fr';

const NBSP = String.fromCharCode(0xa0); // no-break space
const NNBSP = String.fromCharCode(0x202f); // narrow no-break space

// ---- Parts, from the most specific scale to the widest ----------------------------------------
export const PARTIES: readonly { id: SectionRapport; titre: string; court: string }[] = [
	{ id: 'batiment', titre: 'Le bâti et l’énergie', court: 'Bâti' },
	{ id: 'risques', titre: 'Le terrain et les risques', court: 'Terrain' },
	{ id: 'marche', titre: 'Le prix', court: 'Prix' },
	{ id: 'vivre', titre: 'Le quartier', court: 'Quartier' }
];

// ---- Numbers and dates ------------------------------------------------------------------------
const MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

// French numbers and distances live in `lib/utils/nombre-fr.ts` (04.8-03, D-36), re-exported here so the
// importers of this module are unchanged.
export { nombreFr, distanceFr } from '$lib/utils/nombre-fr';

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_MOIS = /^(\d{4})-(\d{2})$/;

/** « 26 août 2026 », « août 2026 » ; a served text that is not a date is returned as is. */
export function dateFr(iso: string): string {
	const j = ISO_DATE.exec(iso);
	if (j) return `${Number(j[3]) === 1 ? '1er' : Number(j[3])} ${MOIS[Number(j[2]) - 1]} ${j[1]}`;
	const m = ISO_MOIS.exec(iso);
	if (m) return `${MOIS[Number(m[2]) - 1]} ${m[1]}`;
	return iso;
}


// ---- Written scale ----------------------------------------------------------------------------
/** Written scale of a value. */
export function libelleEchelle(g: GranulariteRapport, commune: string | null): string {
	switch (g.niveau) {
		case 'adresse':
			return 'à l’adresse';
		case 'batiment':
			return 'bâtiment';
		case 'parcelle':
			return 'parcelle';
		case 'rayon':
			return g.rayon_m !== null ? `rayon de ${distanceFr(g.rayon_m)}` : 'autour de l’adresse';
		case 'station':
			return 'station de mesure la plus proche';
		case 'udi':
			return 'unité de distribution d’eau';
		case 'iris':
			return 'quartier (IRIS)';
		case 'arrondissement':
			return 'tout l’arrondissement';
		case 'commune':
			return commune ? `toute la commune de ${commune}` : 'toute la commune';
		case 'departement':
			return 'tout le département';
		default:
			return `échelle${NBSP}: ${humaniser(g.niveau).toLowerCase()}`;
	}
}

// ---- Served codes, worded ---------------------------------------------------------------------
const MOTIFS: Record<string, string> = {
	dpe_plusieurs_batiments: 'Ces DPE peuvent venir de plusieurs bâtiments',
	aucun_batiment_au_point: 'Aucun bâtiment au point',
	plusieurs_batiments_sur_la_parcelle: 'Plusieurs bâtiments sur la parcelle : celui de l’adresse n’est pas établi',
	plusieurs_batiments_rnb_a_l_adresse: 'Plusieurs bâtiments du registre national portent cette adresse',
	point_hors_parcelle: 'Le point de l’adresse ne tombe dans aucune parcelle',
	parcelle_indeterminee: 'La parcelle de l’adresse n’est pas déterminée',
	parcelles_multiples: 'Plusieurs parcelles possibles',
	parcelle_non_verifiee: 'Parcelle non vérifiée',
	position_indeterminee: 'La position de l’adresse n’est pas déterminée',
	liens_rnb_indisponibles: 'Liens au registre des bâtiments indisponibles',
	adresse_non_verifiable: 'Adresse déclarée non vérifiable',
	meme_annee_pour_tous: 'Même année pour tous les bâtiments du groupe',
	aucun_batiment_bdnb: 'Aucun bâtiment connu de la BDNB',
	annee_non_renseignee: 'Année non renseignée',
	aucun_document_publie: 'Aucun document publié',
	aucun_document_valide: 'Aucun document valide',
	aucune_offre_publiee: 'Aucune offre publiée',
	amont_indisponible: 'La source n’a pas répondu',
	lecture_indisponible: 'Lecture indisponible',
	import_absent: 'Données pas encore importées pour ce département',
	horaires_gtfs_non_importes: 'Horaires des lignes pas encore importés',
	moyennes_geodair_non_importees: 'Moyennes annuelles Geodair pas encore importées',
	loyers_reference_non_importes: 'Loyers de référence pas encore importés',
	majoration_non_importee: 'Majoration pas encore importée',
	mandat_non_importe: 'Mandat du syndic pas encore importé',
	calendrier_outre_mer_non_servi: 'Calendrier non servi outre-mer',
	carreau_impute: 'Revenu imputé sur ce carreau : valeur non reprise',
	commune_indeterminee: 'La commune de l’adresse n’est pas déterminée',
	departement_non_heberge: 'Département pas encore couvert',
	taux_manquants_au_departement: 'Taux manquants pour ce département',
	temporary_error: 'La source n’a pas répondu',
	theme_absent: 'Thème non servi',
	hors_couverture: 'Hors couverture',
	out_of_coverage: 'Hors couverture',
	not_available: 'Non publiée',
	not_reported: 'Non renseigné',
	insufficient_sample: 'Effectif insuffisant',
	zero_denominator: 'Sans base de calcul',
	not_applicable: 'Sans objet',
	not_qualified: 'Non qualifié',
	suppressed: 'Secret statistique',
	loyer_non_publie: 'Loyer non publié',
	revenu_non_publie: 'Revenu non publié',
	taux_non_publie: 'Taux non publié',
	zone_non_publiee: 'Zone non publiée',
	categorie_non_couverte: 'Catégorie non couverte',
	categories_non_couvertes: 'Catégories non couvertes',
	commune_errial_differente: 'L’état des risques porte sur une autre commune',
	parcelle_errial_absente: 'L’état des risques ne cite pas de parcelle',
	parcelle_errial_differente: 'L’état des risques porte sur une autre parcelle',
	poids_incomplets: 'Pondération incomplète',
	zones_multiples: 'Plusieurs zones au point',
	indeterminee: 'indéterminé',
	unique: 'unique'
};

/** « plusieurs_batiments_sur_la_parcelle » → its sentence, or the code made readable. */
export function libelleCode(code: string): string {
	const libelle = MOTIFS[code] ?? LIBELLES_DETAILS[code];
	if (libelle) return libelle;
	sansLibelle?.add(code);
	return humaniser(code);
}

export function humaniser(code: string): string {
	const t = code.replace(/_/g, ' ').trim();
	return t.charAt(0).toUpperCase() + t.slice(1);
}

// ---- Status -------------------------------------------------------------------------------------
import { LIBELLE_STATUT, type CleStatut, type Statut } from './statut';
export { LIBELLE_STATUT, type CleStatut, type Statut } from './statut';

const estServi = (etat: EtatTheme): boolean => etat === 'ok' || etat === 'zero';

/**
 * Badge of a row, from its served state and tone (the API serves the tone the page shows, 04.7-17).
 * A gap says its served reason when it is short enough to be a badge.
 */
export function statutDe(t: ThemeRapport): Statut | null {
	switch (t.etat) {
		case 'panne':
			return { cle: 'panne', texte: LIBELLE_STATUT.panne };
		case 'manque':
			return { cle: 'manque', texte: t.etat_libelle && t.etat_libelle.length <= 24 ? t.etat_libelle : LIBELLE_STATUT.manque };
		case 'hors_couverture':
			return { cle: 'hors_couverture', texte: LIBELLE_STATUT.hors_couverture };
		case 'non_concerne':
			return { cle: 'non_concerne', texte: LIBELLE_STATUT.non_concerne };
		case 'en_cours':
			return { cle: 'en_cours', texte: LIBELLE_STATUT.en_cours };
	}
	if (t.ton === 'vigilance') return { cle: 'vigilance', texte: LIBELLE_STATUT.vigilance };
	if (t.ton === 'rassurant') return { cle: 'rassurant', texte: LIBELLE_STATUT.rassurant };
	if (t.etat === 'zero') return { cle: 'zero', texte: LIBELLE_STATUT.zero };
	return null;
}

/**
 * Reason of a theme in words: the served wording of a gap or of an out of coverage (04.7-17), none
 * when it is not served; for the other states, which the API does not word, the code made readable.
 */
export function motifDe(t: ThemeRapport): string | null {
	if (t.etat === 'manque' || t.etat === 'hors_couverture') return t.etat_libelle;
	if (t.etat === 'non_concerne' && t.etat_libelle) return t.etat_libelle;
	return t.motif ? libelleCode(t.motif) : null;
}

const CLES_SANS_VALEUR: ReadonlySet<CleStatut> = new Set(['manque', 'hors_couverture', 'panne', 'non_concerne', 'en_cours']);

/**
 * Headline of a row (4.9 lot 5, « un état, un endroit »): a state without value is said by its badge, so
 * a headline that only repeats that word, or the generic word of that state, is not written beside it.
 * Any other served headline stays: the front never hides what the API says (D-08).
 */
export function accrocheDeLigne(t: ThemeRapport): string | null {
	const statut = statutDe(t);
	if (!statut || !CLES_SANS_VALEUR.has(statut.cle)) return t.accroche;
	return t.accroche === statut.texte || t.accroche === LIBELLE_STATUT[statut.cle] ? null : t.accroche;
}

/**
 * The served reason said on the row, when the badge does not already say it. A2 of the 4.9 inventory:
 * « Hors couverture » and « Source indisponible » could be read as « no risk », so they keep it in sight;
 * « Non publié » and « Sans objet » leave it to the detail, where `phraseMotif` says it.
 */
export function raisonDeLigne(t: ThemeRapport): string | null {
	const statut = statutDe(t);
	if (t.etat_libelle === null || t.etat_libelle === statut?.texte) return null;
	return statut?.cle === 'manque' || statut?.cle === 'non_concerne' ? null : t.etat_libelle;
}

/** The reason of a gap as a sentence for the detail; none when the row already says it (`dejaDit`). */
export function phraseMotif(t: ThemeRapport, ...dejaDit: (string | null)[]): string | null {
	const dit = t.etat !== 'ok' || t.chiffres.length === 0 ? motifDe(t) : null;
	if (dit === null) return null;
	const phrase = /[.!?…]$/.test(dit) ? dit : `${dit}.`;
	return dejaDit.some((d) => d === dit || d === phrase) ? null : phrase;
}

// ---- Figures ------------------------------------------------------------------------------------
/** A figure's value with its unit, as the detail shows it (no label). */
export function valeurChiffre(c: ChiffreRapport): string {
	const v = c.valeur;
	if (typeof v === 'string') {
		if (c.unite === 'date') return dateFr(v);
		return v;
	}
	switch (c.unite) {
		case 'count':
			return nombreFr(v, 0);
		case 'euro_per_m2':
			return `${nombreFr(v, 0)}${NBSP}€/m²`;
		case 'euro_per_m2_mois':
			// Served with one decimal, never padded, as the commune fiche writes it.
			return `${nombreFr(v)}${NBSP}€/m²/mois`;
		case 'euro_par_an':
			return `${nombreFr(v, 0)}${NBSP}€/an`;
		case 'pourcent':
			return `${nombreFr(v)}${NBSP}%`;
		case 'pour_mille':
			return `${nombreFr(v)}${NBSP}‰`;
		case 'degre_francais':
			return `${nombreFr(v)}${NBSP}°f`;
		case 'indice_sur_6':
			return `${nombreFr(v, 0)}${NBSP}sur${NBSP}6`;
		case 'metre':
			return distanceFr(v);
		case 'm2':
			return `${nombreFr(v, 0)}${NBSP}m²`;
		case 'annee':
			return String(v);
		case 'classe_dpe':
			return String(v);
		case 'none':
			return nombreFr(v);
		default:
			return c.unite ? `${nombreFr(v)}${NBSP}${c.unite}` : nombreFr(v);
	}
}

// ---- Attachment -------------------------------------------------------------------------------
const MOT_CONFIANCE = { sure: 'sûr', probable: 'probable', incertaine: 'incertain' } as const;
export const motConfiance = (c: keyof typeof MOT_CONFIANCE): string => MOT_CONFIANCE[c];

/**
 * Alert line of a row whose attachment is not sure, as the API serves it (04.7-17); null when it is
 * sure, absent or not served. The detail still says the served confidence of the link.
 */
export function alerteDe(t: ThemeRapport): string | null {
	const r = t.rattachement;
	return r && r.confiance !== 'sure' ? r.alerte : null;
}

/** Partial outage of a served row (a sub-source did not answer): said, never hidden. */
export function lecturePartielle(t: ThemeRapport): string | null {
	if (t.pannes_partielles.length === 0) return null;
	return `Lecture partielle${NBSP}: ${t.pannes_partielles.map((p) => libelleCode(p).toLowerCase()).join(', ')} sans réponse.`;
}

// Short source names for the closed row (A2); the full name, millésime and link stay in the detail.
const ETIQUETTES_SOURCES: Record<string, string> = {
	dvf: 'DVF',
	dpe: 'ADEME',
	georisques: 'Géorisques',
	rnb: 'RNB',
	bdnb: 'BDNB',
	batiments: 'IGN',
	cadastre: 'Cadastre',
	urbanisme: 'IGN',
	connectivite: 'Arcep',
	antennes: 'ANFR',
	transports: 'transport.data.gouv',
	loyers: 'Carte des loyers',
	zonage_abc: 'Ministère du Logement',
	fiscalite: 'DGFiP',
	revenus: 'INSEE',
	equipements: 'INSEE',
	copropriete: 'ANAH'
};
export const etiquetteSource = (s: { cle: string; nom: string; producteur: string }): string => ETIQUETTES_SOURCES[s.cle] ?? (s.producteur || s.nom);

export interface SourceListee {
	cle: string;
	nom: string;
	producteur: string;
	url: string | null;
	licence: string | null;
	/** Most recent millésime served for this source, or null when none is published. */
	millesime: string | null;
	direct: boolean;
	criteres: string[];
}

/** Short label of a served source key (`sources_en_panne`): the label of that source in the themes. */
export function etiquetteCleSource(cle: string, themes: readonly ThemeRapport[]): string {
	const s = themes.flatMap((t) => t.sources).find((x) => x.cle === cle);
	return s ? etiquetteSource(s) : (ETIQUETTES_SOURCES[cle] ?? cle);
}

/** The sources of the fiche, once each, with the criteria they serve (legend at the end, print). */
export function sourcesDuRapport(themes: readonly ThemeRapport[]): SourceListee[] {
	const parCle = new Map<string, SourceListee>();
	for (const t of themes) {
		for (const s of t.sources) {
			const vu = parCle.get(s.cle) ?? { cle: s.cle, nom: s.nom, producteur: s.producteur, url: s.url, licence: s.licence, millesime: null, direct: false, criteres: [] };
			if (s.millesime && (!vu.millesime || s.millesime > vu.millesime)) vu.millesime = s.millesime;
			if (s.mode === 'direct') vu.direct = true;
			if (!vu.criteres.includes(t.titre)) vu.criteres.push(t.titre);
			parCle.set(s.cle, vu);
		}
	}
	return [...parCle.values()];
}

// ---- Summary --------------------------------------------------------------------------------------
export interface ResumeFiche {
	pointsAdresse: ThemeRapport[];
	pointsSecteur: ThemeRapport[];
	favorablesAdresse: ThemeRapport[];
	favorablesSecteur: ThemeRapport[];
	pannes: ThemeRapport[];
	sourcesEnPanne: string[];
	partiels: ThemeRapport[];
	sansObjet: ThemeRapport[];
	/** Missing or out of coverage: no answer to read, never a « nothing to report ». */
	sansValeur: ThemeRapport[];
	/** Still being read (R-07): no answer yet, neither an outage nor a gap. */
	enCours?: ThemeRapport[];
	/** Criteria that carry an answer (served, zero, or not applicable here), all, then by column. */
	lus: number;
	lusAdresse: number;
	lusSecteur: number;
	total: number;
}

/**
 * Summary of the fiche as the API serves it (04.7-17): its lists name themes by id and are brought
 * back to the rows the page renders, in the served order; its counts and its split between the
 * address (radius up to 1 km) and the area or commune are the API's, never computed again. Null
 * when the report serves no full summary: the page then says so instead of filling it itself.
 */
export function lireResume(servi: ResumeServi | null, themes: readonly ThemeRapport[]): ResumeFiche | null {
	if (!servi) return null;
	const parId = new Map(themes.map((t) => [t.id, t]));
	const rendus = (ids: readonly string[]): ThemeRapport[] =>
		[...new Set(ids)].map((id) => parId.get(id)).filter((t): t is ThemeRapport => t !== undefined);
	return {
		pointsAdresse: rendus(servi.pointsAdresse),
		pointsSecteur: rendus(servi.pointsSecteur),
		favorablesAdresse: rendus(servi.favorablesAdresse),
		favorablesSecteur: rendus(servi.favorablesSecteur),
		pannes: rendus(servi.pannes),
		sourcesEnPanne: [...new Set(servi.sourcesEnPanne.map((cle) => etiquetteCleSource(cle, themes)))],
		partiels: rendus(servi.partiels),
		sansObjet: rendus(servi.sansObjet),
		sansValeur: rendus(servi.sansValeur),
		enCours: rendus(servi.enCours ?? []),
		lus: servi.lus,
		lusAdresse: servi.lusAdresse,
		lusSecteur: servi.lusSecteur,
		total: servi.total
	};
}

const pluriel = (n: number, un: string, plusieurs: string): string => (n > 1 ? plusieurs : un);
export const criteres = (n: number): string => `${n} ${pluriel(n, 'critère', 'critères')}`;

/**
 * Label of the fold of « Sans valeur ici » (A4 of the 4.9 inventory): the served gaps, the ones out of
 * coverage set apart, since they must not read as « no risk » once folded. `apresPannes`: outages are
 * listed above the fold, and the word « autres » keeps the two counts of the block from clashing.
 */
export function repliSansValeur(sansValeur: readonly ThemeRapport[], apresPannes: boolean): string {
	const n = sansValeur.length;
	const horsCouverture = sansValeur.filter((t) => t.etat === 'hors_couverture').length;
	const autres = apresPannes ? pluriel(n, 'autre ', 'autres ') : '';
	const dont = horsCouverture === 0 ? '' : horsCouverture < n ? `, dont ${horsCouverture} hors couverture` : pluriel(n, ', hors couverture', ', tous hors couverture');
	return `${n} ${autres}${pluriel(n, 'critère', 'critères')} sans valeur ici${dont}`;
}

/**
 * The mention of the head while sources are still read (R-07), then how the reads ended. `attendues`: the
 * criteria the first answer served « en cours »; `restantes`: those still so; `pannes`: those that ended as
 * an outage; `interrompue`: the follow-up failed. Null when the first answer was complete. Wordings
 * proposed, to be validated by Ianis.
 */
export function mentionLectures(e: { attendues: number; restantes: number; pannes: number; interrompue: boolean }): string | null {
	if (e.attendues === 0) return null;
	if (e.restantes > 0) {
		if (e.interrompue) return `La lecture continue${NBSP}: rechargez la fiche dans quelques secondes.`;
		return `${criteres(e.restantes)} ${pluriel(e.restantes, 'attend', 'attendent')} encore la réponse de ${pluriel(e.restantes, 'sa', 'leur')} source.`;
	}
	if (e.pannes === 0) return 'Toutes les sources ont répondu.';
	return `Lecture terminée${NBSP}: ${criteres(e.pannes)} ${pluriel(e.pannes, 'reste', 'restent')} sans réponse de ${pluriel(e.pannes, 'sa', 'leur')} source.`;
}

/** Label of the reload link of that mention: the only way to the values without JavaScript. */
export const LIEN_RECHARGER_FICHE = 'Recharger la fiche';

export const pointsDeVigilance = (n: number): string => `${n} ${pluriel(n, 'point de vigilance', 'points de vigilance')}`;

/**
 * Head sentence of « En bref », as segments; an outage is always said, never a bare « rien ». The six
 * sentences of UI-SPEC v4 § 7.2: since 04.7-17 only the words changed (« à l’adresse ou autour »,
 * « Rien à l’adresse ni autour »); the branches and the served counts are the same.
 */
export function phraseEnBref(r: ResumeFiche): { texte: string; fort?: boolean }[] {
	const a = r.pointsAdresse.length;
	const c = r.pointsSecteur.length;
	const p = r.pannes.length;
	const perimetre = perimetreSecteur(r.pointsSecteur);
	// Something was not read (an outage, a missing value, out of coverage, a read in progress): never a bare « rien ».
	const incomplet = p > 0 || r.sansValeur.length > 0 || (r.enCours?.length ?? 0) > 0;
	const s: { texte: string; fort?: boolean }[] = [];
	if (r.lus === 0) {
		// Nothing read: the outages are said in the same sentence, never twice.
		s.push({ texte: 'Aucun critère n’a pu être lu à cette adresse' });
		if (p) s.push({ texte: `${NNBSP}; ` }, { texte: `${p} ${pluriel(p, 'critère', 'critères')}`, fort: true }, { texte: ` sans réponse de ${pluriel(p, 'sa source', 'leur source')}` });
		s.push({ texte: '.' });
		return s;
	} else if (a === 0 && c === 0) s.push({ texte: incomplet ? 'Rien à signaler parmi les critères lus' : `Rien à signaler, ni à l’adresse ou autour, ni ${perimetre}` });
	else if (a === 0) s.push({ texte: `Rien à l’adresse ni autour${incomplet ? ' parmi les critères lus' : ''}${NNBSP}; ` }, { texte: pointsDeVigilance(c), fort: true }, { texte: ` ${perimetre}` });
	else {
		s.push({ texte: pointsDeVigilance(a), fort: true }, { texte: ' à l’adresse ou autour' });
		if (c) s.push({ texte: ', ' }, { texte: String(c), fort: true }, { texte: ` ${perimetre}` });
		else if (!incomplet) s.push({ texte: `, aucun ${perimetre}` });
	}
	if (p) s.push({ texte: ', et ' }, { texte: `${p} ${pluriel(p, 'critère', 'critères')}`, fort: true }, { texte: ` ${pluriel(p, 'n’a pas pu être lu', 'n’ont pas pu être lus')}.` });
	else s.push({ texte: '.' });
	return s;
}

// ---- Generic details --------------------------------------------------------------------------------
export type NoeudDetail =
	| { type: 'valeur'; libelle: string; valeur: string }
	| { type: 'liste'; libelle: string; items: string[] }
	| { type: 'table'; libelle: string; colonnes: string[]; lignes: string[][]; note: string | null }
	| { type: 'repartition_dpe'; libelle: string; classes: { classe: string; n: number | null }[]; inconnus?: number }
	| { type: 'groupe'; libelle: string; enfants: NoeudDetail[] };

const LIBELLES_CLES: Record<string, string> = {
	repartition: 'Répartition par classe énergie',
	documents_appartement: 'DPE de logements en appartement',
	plus_recent: 'DPE le plus récent',
	batiments: 'Bâtiments',
	rnb_des_documents: 'Bâtiments du registre cités par les DPE',
	documents_sans_rnb: 'DPE sans bâtiment du registre',
	liens_rnb_de_la_cle: 'Bâtiments du registre liés à l’adresse',
	un_seul_batiment_etabli: 'Un seul bâtiment établi',
	documents_ex_aequo: 'DPE de même date',
	date: 'Date',
	document: 'Document',
	etat: 'État',
	motif: 'Motif',
	distance_m: 'Distance',
	liste_incomplete: 'Liste incomplète',
	diffuse: 'Chiffre publié pour la commune',
	contenance_m2: 'Surface cadastrale',
	nb_observations: 'Observations',
	charges_comprises: 'Charges comprises',
	type_habitat: 'Type de logement',
	loyer_m2_bas: 'Bas de fourchette',
	loyer_m2_haut: 'Haut de fourchette',
	taux_tfpb: 'Taxe foncière sur le bâti',
	taux_tfpnb: 'Taxe foncière sur le non-bâti',
	taux_teom: 'Taxe d’ordures ménagères',
	taux_th_rs: 'Taxe d’habitation (résidences secondaires)',
	nom_epci: 'Intercommunalité',
	revenu_median: 'Revenu déclaré médian',
	taux_pauvrete: 'Taux de pauvreté',
	indice_gini: 'Inégalités de revenus (indice de Gini)',
	part_imposee: 'Part des ménages imposés',
	periode_construction: 'Période de construction',
	mat_mur_txt: 'Matériau des murs',
	mat_toit_txt: 'Matériau du toit',
	nom_copropriete: 'Nom de la copropriété',
	numero_immatriculation: 'Numéro d’immatriculation',
	nb_lots_total: 'Lots',
	nb_lots_habitation_strict: 'Lots d’habitation',
	debit_max: 'Débit maximal',
	supports_5g: 'Supports 5G',
	par_domaine: 'Par domaine',
	date_arrete: 'Date de l’arrêté',
	prix: 'Prix',
	prix_m2: 'Prix au m²',
	surface_batie: 'Surface',
	surface_habitable: 'Surface',
	date_mutation: 'Date de vente',
	date_etablissement: 'Établi le'
};
function libelleCle(cle: string): string {
	const libelle = LIBELLES_CLES[cle] ?? LIBELLES_DETAILS[cle];
	if (libelle) return libelle;
	sansLibelle?.add(cle);
	return humaniser(cle);
}

// Codes that fell back to `humaniser` during one `codesSansLibelle` call (a raw label such as
// « Exception copropriete »); null otherwise, so the page itself never collects anything.
let sansLibelle: Set<string> | null = null;

/**
 * The served codes (keys, snake_case values) that the details of a theme would show as a raw label:
 * those that have neither a key label nor a reason label, single words that read as is aside. The guard of the details labels: a code
 * served by the API needs its words here before it reaches the page.
 */
export function codesSansLibelle(details: Record<string, unknown>, themeId: string | null = null): string[] {
	sansLibelle = new Set();
	try {
		aplatirDetails(details, 0, false, themeId);
		return [...sansLibelle].filter((code) => !MOTS_LISIBLES.has(code)).sort();
	} finally {
		sansLibelle = null;
	}
}

// A served value that is one lowercase word (« value », « agglomeration », « moyen »): the details show it
// as served. Collected only during one `valeursBrutes` call, like `sansLibelle`.
const UN_MOT_BRUT = /^[a-zà-ÿ][a-zà-ÿ0-9-]*$/;
let motsBruts: Set<string> | null = null;

/**
 * The one-word values the details of a theme would show raw, sorted. The guard of the one-word values:
 * each one found is either written in words (`VALEURS_LIBELLEES`) or listed as readable (`VALEURS_LISIBLES`) in `libelles-details.ts`.
 */
export function valeursBrutes(details: Record<string, unknown>, themeId: string | null = null): string[] {
	motsBruts = new Set();
	try {
		aplatirDetails(details, 0, false, themeId);
		return [...motsBruts].sort();
	} finally {
		motsBruts = null;
	}
}

// Keys of a sale that would reveal a masked price (isolated recent sale): never rendered when
// `prix_affiche` is false, whatever the theme.
const CLES_PRIX = new Set(['prix', 'prix_m2', 'valeur_fonciere', 'prix_eur', 'montant']);
const CLES_TECHNIQUES = new Set(['prix_affiche', 'minimisation']);
const CLASSES_DPE = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

function valeurPrimitive(cle: string, v: unknown): string | null {
	if (v === null || v === undefined) return null; // never a fabricated zero (A5)
	if (typeof v === 'boolean') {
		// A technical flag is said only when it carries a reserve (owner review of 09/11): never « non ».
		const reserve = RESERVES_LECTURE[cle];
		if (reserve) return v === reserve.quand ? 'oui' : null;
		if (!v && DRAPEAUX_MUETS_SI_FAUX.has(cle)) return null;
		return v ? 'oui' : 'non';
	}
	if (typeof v === 'number') {
		if (cle === 'distance_m' || cle.endsWith('_m')) return distanceFr(v);
		if (cle.endsWith('_m2') && !cle.startsWith('prix') && !cle.startsWith('loyer')) return `${nombreFr(v, 0)}${NBSP}m²`;
		return nombreFr(v);
	}
	if (typeof v === 'string') {
		if (ISO_DATE.test(v) || ISO_MOIS.test(v)) return dateFr(v);
		if (/^[a-z0-9]+(_[a-z0-9]+)+$/.test(v) || MOTIFS[v]) return libelleCode(v);
		if (VALEURS_LIBELLEES[v]) return VALEURS_LIBELLEES[v];
		if (UN_MOT_BRUT.test(v)) motsBruts?.add(v);
		return v;
	}
	return null;
}

const estPrimitive = (v: unknown): boolean => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const estObjet = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

/** An object that withholds its price: the price keys are replaced by a single readable mention. */
const prixMasque = (o: Record<string, unknown>): boolean => o.prix_affiche === false;

/**
 * A nested value as one line of text (« Valeur : 61 ; Unité : mg/L »), so that nothing served is
 * dropped by the layout. A masked price stays masked in every descendant.
 */
function texteImbrique(v: unknown, cle: string, masque: boolean): string | null {
	if (estPrimitive(v)) return valeurPrimitive(cle, v);
	if (Array.isArray(v)) {
		const items = v.map((x) => texteImbrique(x, cle, masque)).filter((x): x is string => x !== null);
		return items.length ? items.join(', ') : null;
	}
	if (!estObjet(v)) return null;
	const cache = masque || prixMasque(v);
	const parties = Object.entries(v).flatMap(([k, x]) => {
		if (CLES_TECHNIQUES.has(k)) return [];
		if (CLES_PRIX.has(k) && cache) return k === 'prix' || k === 'valeur_fonciere' ? [`${libelleCle(k)}${NBSP}: prix non affiché`] : [];
		const t = texteImbrique(x, k, cache);
		return t === null ? [] : [`${libelleCle(k)}${NBSP}: ${t}`];
	});
	return parties.length ? parties.join(`${NNBSP}; `) : null;
}

function cellule(o: Record<string, unknown>, cle: string, masque: boolean): string {
	if (CLES_PRIX.has(cle) && masque) return cle === 'prix' || cle === 'valeur_fonciere' ? 'Prix non affiché' : '—';
	return texteImbrique(o[cle], cle, masque) ?? '—';
}

/**
 * Generic rendering of a theme's `details`, at most three levels deep: primitives as labelled
 * values (nulls skipped, never written 0), lists of primitives as lists, lists of objects as tables,
 * objects as groups, an A–G count object as the DPE distribution. Masked prices never appear.
 */
export function aplatirDetails(details: Record<string, unknown>, profondeur = 0, masqueHerite = false, themeId: string | null = null): NoeudDetail[] {
	const noeuds: NoeudDetail[] = [];
	// A price withheld by an object stays withheld in all its descendants.
	const masque = masqueHerite || prixMasque(details);
	for (const [cle, v] of Object.entries(details)) {
		if (CLES_TECHNIQUES.has(cle)) continue;
		const libelle = libelleCle(cle);
		if (CLES_PRIX.has(cle) && masque) {
			if (cle === 'prix' || cle === 'valeur_fonciere') noeuds.push({ type: 'valeur', libelle, valeur: 'Prix non affiché (vente isolée récente)' });
			continue;
		}
		// A raised reserve flag reads as its label and the reason why; a flag that is not raised is not shown.
		const reserve = RESERVES_LECTURE[cle];
		if (reserve && typeof v === 'boolean') {
			if (v === reserve.quand) noeuds.push({ type: 'valeur', libelle: reserve.libelle, valeur: raisonReserve(cle, themeId) });
			continue;
		}
		if (estPrimitive(v)) {
			const valeur = valeurPrimitive(cle, v);
			if (valeur !== null) noeuds.push({ type: 'valeur', libelle, valeur });
			continue;
		}
		if (Array.isArray(v)) {
			if (v.length === 0) continue;
			if (v.every(estPrimitive)) {
				const items = v.map((x) => valeurPrimitive(cle, x)).filter((x): x is string => x !== null);
				if (items.length) noeuds.push({ type: 'liste', libelle, items });
			} else if (v.every(estObjet)) {
				const objets = v as Record<string, unknown>[];
				const cache = (o: Record<string, unknown>) => masque || prixMasque(o);
				// Every served column, nested values written as text: the table scrolls, it never drops.
				const colonnes = [...new Set(objets.flatMap((o) => Object.keys(o)))].filter(
					(k) => !CLES_TECHNIQUES.has(k) && objets.some((o) => texteImbrique(o[k], k, cache(o)) !== null || (CLES_PRIX.has(k) && cache(o)))
				);
				const masquees = objets.filter(cache).length;
				noeuds.push({
					type: 'table',
					libelle,
					colonnes: colonnes.map(libelleCle),
					lignes: objets.map((o) => colonnes.map((k) => cellule(o, k, cache(o)))),
					note: masquees ? `${masquees} ${pluriel(masquees, 'prix n’est pas affiché', 'prix ne sont pas affichés')}${NBSP}: vente isolée récente.` : null
				});
			} else {
				const items = v.map((x) => texteImbrique(x, cle, masque)).filter((x): x is string => x !== null);
				if (items.length) noeuds.push({ type: 'liste', libelle, items });
			}
			continue;
		}
		if (estObjet(v)) {
			const cles = Object.keys(v);
			if (cles.length && cles.every((k) => CLASSES_DPE.includes(k) || k === 'unknown') && CLASSES_DPE.some((k) => typeof v[k] === 'number')) {
				// A class not served stays « non servie », never 0; documents without a class are kept.
				const classes = CLASSES_DPE.map((classe) => ({ classe, n: typeof v[classe] === 'number' ? (v[classe] as number) : null }));
				const inconnus = typeof v.unknown === 'number' && v.unknown > 0 ? v.unknown : 0;
				noeuds.push({ type: 'repartition_dpe', libelle, classes, ...(inconnus ? { inconnus } : {}) });
				continue;
			}
			if (profondeur >= 2) {
				const valeur = texteImbrique(v, cle, masque);
				if (valeur !== null) noeuds.push({ type: 'valeur', libelle, valeur });
				continue;
			}
			const enfants = aplatirDetails(v, profondeur + 1, masque, themeId);
			if (enfants.length) noeuds.push({ type: 'groupe', libelle, enfants });
		}
	}
	return noeuds;
}

// ---- Invariants (tested; also usable as runtime guards) -----------------------------------------
/** Scales whose value describes an entity found from the address: its link must be established. */
const ECHELLES_RATTACHEES = new Set(['batiment', 'parcelle']);

/** True when the theme's link to the address is established (sure, or not needed at its scale). */
function lienEtabli(t: ThemeRapport): boolean {
	// The confidence of a link is that of its weakest link (UI-SPEC § 5, rule 4).
	if (t.rattachement) return t.rattachement.confiance === 'sure' && !!t.rattachement.maillons?.every((m) => m.confiance === 'sure');
	return !ECHELLES_RATTACHEES.has(t.granularite.niveau);
}

/**
 * A1: the badge never contradicts the row's own state, tone, figures and attachment. The API serves
 * a coherent tone (04.7-17); this detects a report that would not, it never corrects it.
 */
export function verdictCoherent(t: ThemeRapport): boolean {
	const s = statutDe(t);
	if (ETATS_SANS_REPONSE.has(t.etat)) return t.chiffres.length === 0 && s !== null && s.cle === t.etat;
	if (s?.cle === 'rassurant') return estServi(t.etat) && t.pannes_partielles.length === 0 && lienEtabli(t);
	if (s?.cle === 'vigilance') return estServi(t.etat);
	return true;
}

/**
 * A5: a zero is shown only when the source says zero. A figure 0 needs a served state, and the
 * details never render more zeros than they serve (a null, an absent field never becomes 0).
 */
export function aucunZeroFabrique(t: ThemeRapport): boolean {
	if (t.chiffres.some((c) => c.valeur === 0) && !estServi(t.etat)) return false;
	const zerosServis = (o: unknown): number =>
		Array.isArray(o) ? o.reduce((n: number, x) => n + zerosServis(x), 0) : estObjet(o) ? Object.values(o).reduce((n: number, x) => n + zerosServis(x), 0) : o === 0 ? 1 : 0;
	const zerosRendus = (noeuds: NoeudDetail[]): number =>
		noeuds.reduce((n, x) => {
			if (x.type === 'valeur') return n + (x.valeur === '0' ? 1 : 0);
			if (x.type === 'liste') return n + x.items.filter((i) => i === '0').length;
			if (x.type === 'table') return n + x.lignes.flat().filter((c) => c === '0').length;
			if (x.type === 'repartition_dpe') return n + x.classes.filter((c) => c.n === 0).length;
			if (x.type === 'groupe') return n + zerosRendus(x.enfants);
			return n;
		}, 0);
	return zerosRendus(aplatirDetails(t.details)) <= zerosServis(t.details);
}
