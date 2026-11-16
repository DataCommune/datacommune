// Model of the commune synthesis (UI-SPEC « /commune/[code] » points 3 and 4, D-46). Pure module,
// zero Svelte imports: the page renders what this returns and computes nothing itself.
//
// ORDER IS THE CONTRACT (D-46, E1): ① median price of houses, ② median price of flats, ③ trend,
// ④ dominant DPE class. The mock-up order (population, prices, rent, F/G share) is not used;
// population, rent and the F/G share stay in the themes. `communeKpis` always gives the four; the
// page shows `reperesConnus`, what is known (4.9, lot 3): an absence stays in its theme.
//
// WHAT IS READ (D-51): the dated indicator blocks `indicateurs_dvf` / `indicateurs_dpe` when the
// instance serves them — their rows are the `INDICATOR_SUMMARY_ORDER` of `commune-grid.ts` — and
// the flat columns otherwise, through `etatDeColonne` (D-47). A block served as an absence keeps
// its own state and reason; a null block falls back to the flat column of the same meaning. The
// browser never computes a median, a share or a trend: every figure is the served one, rounded
// for display only by `formatters.ts` at the contract precision.
//
// ABSENCES: the served state is kept as is (insufficient_sample stays insufficient_sample, it is
// never folded into « not reported »), with its reason. An absence the API did not declare is an
// API defect: the line is not rendered, and it is named on the console in development.

import type {
	BlocIndicateur,
	CommuneSynthese,
	EnergyClass,
	EtatAbsenceColonne,
	IndicateurAbsent,
	IndicateursDpe,
	IndicateursDvf,
	NombreOuAbsence,
	SourceAttribution,
	StatistiquesFenetreDvf
} from '$lib/api/commune';
import { INDICATOR_SUMMARY_ORDER, mapCommuneToGrid, type CommuneGridModel } from './commune-grid';
import { etatDeColonne, type ColonnePlate } from './etats-colonnes';
import { definitionPublique, sourcesCommuneParDataset } from './explications';
import { LIBELLE_EN_CLAIR, enClairChamp } from './explications-simples';
import { displayUnit, formatBareNumber, getFieldContract } from './formatters';
import {
	AUCUNE_VENTE,
	dateEnLettres,
	flatCommunePeriod,
	jourEnLettres,
	LIBELLE_PERIODE_ENTAMEE,
	LIBELLE_TENDANCE,
	LIBELLES_MEDIANE,
	LIBELLES_TENDANCE,
	libelleModesDpe,
	LIMITES_DPE,
	limitesDvf,
	METHODE_DPE,
	METHODE_PRIX,
	METHODE_TENDANCE,
	OBJET_TENDANCE,
	PERIODE_ENTAMEE,
	TENDANCE_NON_PUBLIEE,
	tendanceSansBloc,
	variationPubliable
} from './commune-indicators';
import {
	CHAMP_EVOLUTION,
	DEFINITION_TENDANCE_ANNUELLE,
	entreesDetail,
	etatTendance,
	LIMITES_TENDANCE_ANNUELLE,
	methodeTendance,
	OBJET_TENDANCE_ANNUELLE,
	periodeTendance,
	pointsTendance,
	tendanceServie,
	titreDuType,
	typesDeLaTuile,
	type PointTendance
} from './commune-tendance';
import { COPIE_DVF } from './dvf-attribution-view';

export { AUCUNE_VENTE, PERIODE_ENTAMEE, TENDANCE_NON_PUBLIEE };

/** Every absence state a KPI line can carry: flat-column states (D-47) and block states. */
/** Address landmarks (04-18) also carry an outage or a not-applicable value of the place. */
export type EtatAbsence = EtatAbsenceColonne | IndicateurAbsent['etat'] | 'temporary_error' | 'not_applicable';

export type EtatKpi =
	| { kind: 'valeur'; texte: string; unite: string | null }
	| {
			kind: 'absence';
			etat: EtatAbsence;
			motif: string;
			effectif: number | null;
			/** Help sentence written by the interface, in place of the glossary sentence of the
			 *  state, when the glossary would misstate the cause (null dated block, see lireTendance). */
			aide?: string;
			/** Label decided for this absence in place of the glossary one (C40: the reason of the
			 *  declared sales of an address, as its page says it); the state stays the served one. */
			libelle?: string;
			/** The label is the neutral provisional one (a served reason without wording): marked, and
			 *  no sentence is written, neither the glossary one nor another. */
			provisoire?: boolean;
	  }
	| { kind: 'absence-non-declaree' };

/** What « Voir la donnée telle que l'API la renvoie » shows: the field, its raw value, its state. */
export interface ValeurServie {
	fieldId: string;
	/** Raw served value, JSON-encoded (`null` for an absence): never the state key itself. */
	valeur: string;
	etat: { kind: 'valeur' } | { kind: 'absence'; etat: EtatAbsence; motif: string };
}

export interface KpiLigne {
	/** Row id of the data layer (`INDICATOR_SUMMARY_ORDER`) or flat column read. */
	id: string;
	fieldId: string;
	/** Sub-label when a KPI carries several lines (trend: houses then flats). */
	libelle: string | null;
	/** Label of the field when neither the sub-label nor the tile title says it whole (one field, one label). */
	libelleChamp?: string;
	etat: EtatKpi;
	/** Served size of the population behind the value (« 12 ventes »), when served. */
	effectif: string | null;
	/** Served window of the value, when served. */
	fenetre: string | null;
	/** Exact served windows of a trend line (« précédente / courante »). Never shown on the card:
	 *  two absent trend lines are merged only when these are identical too (03-17 B1). */
	periodeServie?: string | null;
	/** Served qualification of a trend, when served. */
	qualification?: string | null;
	/** DPE classes to show as tags, in the served A→G order. */
	classes?: EnergyClass[];
	/** Served class shown as a tag in the head of the card (document landmarks ① and ②, 04-04). */
	etiquette?: EnergyClass | null;
	/** Yearly medians drawn as a curve under the value (yearly price series); empty: no curve. */
	serie?: PointTendance[];
	/** Period read on the card itself, under the value (« 2021 → 2025 »). */
	periodeVisible?: string;
	/** Kept in « L'essentiel » without a value: the points of a yearly series whose change is not written. */
	enSynthese?: boolean;
	brut: ValeurServie;
}

export interface SourceKpi {
	nom: string;
	url: string | null;
}

/** One `<dt>`/`<dd>` pair of the source panel, in the UI-SPEC order. */
export type EntreeVolet =
	/** `url`: served https link of the entry (« Référence », source of an address value, 04-18). */
	| { terme: string; valeur: string; url?: string | null }
	| { terme: 'État et motif'; absences: { libelle: string | null; etat: Extract<EtatKpi, { kind: 'absence' }> }[] };

export interface KpiCommune {
	id: 'prix-maisons' | 'prix-appartements' | 'tendance' | 'dpe-dominant';
	rang: 1 | 2 | 3 | 4;
	libelle: string;
	/** One sentence read on the card under its title (04.7-15, Origami R8): value + definition + base. */
	definition: string;
	lignes: KpiLigne[];
	source: SourceKpi | null;
	volet: EntreeVolet[];
}

/** A caveat of « Ce qui mérite une lecture attentive », tied to the KPI it qualifies. */
export interface ReserveSynthese {
	kpi: KpiCommune['id'];
	ancre: string;
	texte: string;
}

type Sources = SourceAttribution[] | null | undefined;

/** Served source of a dataset: self-keyed provenance first, else the name-zipped attribution. */
interface SourceServie {
	nom?: string;
	url?: string | null;
	dataset?: string;
	derniere_importation_reussie?: string | null;
}

function sourceDuDataset(sources: Sources, dataset: string): SourceServie | null {
	const servies = (sources ?? []) as SourceServie[];
	const parCle = servies.find((source) => source?.dataset === dataset);
	if (parCle) return parCle;
	return (sourcesCommuneParDataset(sources)[dataset] as SourceServie | undefined) ?? null;
}

interface Provenance {
	source: SourceKpi | null;
	importation: string | null;
}

function provenanceColonne(sources: Sources, dataset: string): Provenance {
	const servie = sourceDuDataset(sources, dataset);
	return {
		source: servie?.nom ? { nom: servie.nom, url: servie.url ?? null } : null,
		importation: dateFr(servie?.derniere_importation_reussie)
	};
}

function provenanceBloc(block: BlocIndicateur): Provenance {
	const noms = [...new Set(block.provenance.map((item) => item.nom).filter(Boolean))];
	const reference = block.provenance[0]?.reference ?? null;
	const url = reference && block.provenance.every((item) => item.reference === reference) ? reference : null;
	const dates = block.provenance.map((item) => item.date_import).filter((d): d is string => Boolean(d)).sort();
	return { source: noms.length ? { nom: noms.join(' · '), url } : null, importation: dateFr(dates.at(-1)) };
}

const SANS_PROVENANCE: Provenance = { source: null, importation: null };

// `formatters.ts` spells the flat columns' `euro_per_m2`; the dated blocks' contract writes the
// same unit `EUR/m2`, which its suffix table does not know. Adding it there would change the
// rendering of every existing grid cell of those blocks, so the KPI maps it here.
const UNITES_BLOCS: Readonly<Record<string, string>> = { 'EUR/m2': '€/m²' };

function unite(fieldId: string): string | null {
	return displayUnit(fieldId) ?? UNITES_BLOCS[getFieldContract(fieldId).unit] ?? null;
}

function valeur(fieldId: string, brute: number | string, signe = false): EtatKpi {
	// A class or a label is served as text: only numbers go through the contract precision.
	const texte = getFieldContract(fieldId).formatter === 'text' ? String(brute) : formatBareNumber(fieldId, brute);
	return { kind: 'valeur', texte: signe && Number(brute) > 0 ? `+${texte}` : texte, unite: unite(fieldId) };
}

function absence(etat: EtatAbsence, motif: string, effectif: number | null = null): EtatKpi {
	return { kind: 'absence', etat, motif, effectif };
}

function estBlocAbsent(value: unknown): value is IndicateurAbsent {
	return typeof value === 'object' && value !== null && 'etat' in value && !('version_contrat' in value);
}

function brut(fieldId: string, etat: EtatKpi, raw: unknown): ValeurServie {
	return {
		fieldId,
		valeur: etat.kind === 'valeur' ? JSON.stringify(raw) : 'null',
		etat: etat.kind === 'absence' ? { kind: 'absence', etat: etat.etat, motif: etat.motif } : { kind: 'valeur' }
	};
}

function ligne(partial: Omit<KpiLigne, 'brut'>, raw: unknown): KpiLigne {
	return { ...partial, brut: brut(partial.fieldId, partial.etat, raw) };
}

function ventes(fieldId: string, n: number): string {
	return `${formatBareNumber(fieldId, n)} ${n === 1 ? 'vente' : 'ventes'}`;
}

// Months in full on a card (F15, 04.7-15): « juillet 2024 », never « juil. 2024 ».
const MOIS_LONG = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/**
 * Short period of a served window for the one line under a KPI value (03-17 R-1, option (b) chosen
 * by Ianis): « 2025 » when the served window is exactly one calendar year, « juillet 2024 – juin
 * 2025 » when it is made of whole months, else « du {début} au {fin} » (the end is the day before
 * the served exclusive end). A presentation of the served dates, never a rounded or invented year;
 * the source panel keeps the exact served form (« {debut_inclus} → {fin_exclue} (fin exclue) »).
 */
export function periodeCourte(fenetre: { debut_inclus: string; fin_exclue: string }): string {
	const debut = new Date(`${fenetre.debut_inclus}T00:00:00Z`);
	const fin = new Date(`${fenetre.fin_exclue}T00:00:00Z`);
	if (Number.isNaN(debut.getTime()) || Number.isNaN(fin.getTime())) {
		return `${fenetre.debut_inclus} → ${fenetre.fin_exclue} (fin exclue)`;
	}
	const mois = (fin.getUTCFullYear() - debut.getUTCFullYear()) * 12 + fin.getUTCMonth() - debut.getUTCMonth();
	if (debut.getUTCDate() === 1 && fin.getUTCDate() === 1 && mois >= 1) {
		if (mois === 12 && debut.getUTCMonth() === 0) return String(debut.getUTCFullYear());
		const dernierMois = new Date(Date.UTC(fin.getUTCFullYear(), fin.getUTCMonth() - 1, 1));
		return mois === 1 ? MOIS_LONG.format(debut) : `${MOIS_LONG.format(debut)} – ${MOIS_LONG.format(dernierMois)}`;
	}
	fin.setUTCDate(fin.getUTCDate() - 1);
	return `du ${jourEnLettres(debut)} au ${jourEnLettres(fin)}`;
}

// A served flat window of days, both bounds included, `AAAA-MM-JJ → AAAA-MM-JJ` (`fenetre_mediane_dvf`
// « Bornes inclusives », C29; the DVF import label of C24, first and last day of the published sales).
const FENETRE_INCLUSIVE = /^(\d{4}-\d{2}-\d{2}) → (\d{4}-\d{2}-\d{2})$/;

/**
 * Card form of a served flat period (N-2, plan 04.5-03): an inclusive window « du 31 décembre 2024 au
 * 31 décembre 2025 », the two served days, the end included (unlike `periodeCourte`, whose served end is
 * exclusive, no day is subtracted); a served day « 26 septembre 2026 ». Any other label is kept as served.
 * Shared by the commune markers and the reference card ④ of an address (the same served window); the
 * source panels and the export keep the served form.
 */
export function periodeEnLettres(periode: string): string {
	const bornes = periode.match(FENETRE_INCLUSIVE);
	if (!bornes) return dateEnLettres(periode) ?? periode;
	const [debut, fin] = [dateEnLettres(bornes[1]), dateEnLettres(bornes[2])];
	return debut && fin ? `du ${debut} au ${fin}` : periode;
}

// A day on a card, served (ISO) or spelled (« 31 décembre 2024 », « 1er octobre 2026 »).
const DATE_DE_CARTE = /(\d{4}-\d{2}-\d{2}|(?<![\p{L}\p{N}])\d{1,2}(?:er)? \p{L}+\.? \d{4}(?!\p{N}))/u;

/** The short line of a card cut around its days: the line may wrap between two days, never inside one. */
export function fragmentsDeDates(texte: string): { texte: string; date: boolean }[] {
	return texte.split(DATE_DE_CARTE).flatMap((fragment, index) => (fragment ? [{ texte: fragment, date: index % 2 === 1 }] : []));
}

/** Short period of a served window for the KPI line; the served reason when no window is served. */
function fenetreCourte(stats: StatistiquesFenetreDvf): string | null {
	return stats.fenetre ? periodeCourte(stats.fenetre) : stats.motif_periode ?? null;
}

/** Served window, in the wording of the data layer (commune-indicator-presentation.ts). */
function fenetreServie(stats: StatistiquesFenetreDvf): string | null {
	return stats.fenetre
		? `${stats.fenetre.debut_inclus} → ${stats.fenetre.fin_exclue} (fin exclue)`
		: stats.motif_periode ?? null;
}

function signalerAbsenceNonDeclaree(fieldId: string) {
	if (import.meta.env.DEV) console.error(`[commune-kpi] absence non déclarée par l'API : ${fieldId}`);
}

function dateFr(iso: string | null | undefined): string | null {
	if (!iso) return null;
	const date = new Date(iso);
	return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('fr-FR', { timeZone: 'UTC' });
}

/** A flat column read through its declared state; an undeclared absence is signalled. */
function etatColonne(commune: CommuneSynthese, colonne: ColonnePlate, fieldId: string, effectif: number | null = null): EtatKpi {
	const lu = etatDeColonne(commune, colonne);
	if (lu.kind === 'valeur') return valeur(fieldId, lu.valeur as number | string);
	if (lu.kind === 'absence') return absence(lu.etat, lu.motif, effectif);
	signalerAbsenceNonDeclaree(fieldId);
	return { kind: 'absence-non-declaree' };
}

function valeurBrute(commune: CommuneSynthese, colonne: ColonnePlate): unknown {
	return commune[colonne] ?? null;
}

// --- ① ② median prices ---------------------------------------------------------------------

type TypeBien = 'maison' | 'appartement';

const SOUS_LIBELLES = { maison: 'Maisons', appartement: 'Appartements' } as const;
const OBJETS_PRIX = {
	maison: 'Ventes de maisons (mutations DVF) de la commune',
	appartement: 'Ventes d’appartements (mutations DVF) de la commune'
} as const;

function statsDvf(block: IndicateursDvf, type: TypeBien) {
	return type === 'maison' ? block.residentiel_maison : block.residentiel_appartement;
}

interface Lecture {
	/** Title of the KPI when it depends on what is served (marker ④: 03-17 V27, F36). */
	libelle?: string;
	/** Definition that goes with that title, in place of the marker's own. */
	definition?: string;
	/** Object of the panel, in place of the marker's own. */
	objet?: string;
	/** Entries only this reading has, placed before « Limites ». */
	detail?: EntreeVolet[];
	lignes: KpiLigne[];
	provenance: Provenance;
	methode: string | null;
	limites: string | null;
	periode: string | null;
}

function lirePrix(commune: CommuneSynthese, type: TypeBien, sources: Sources): Lecture {
	const [idMediane] = type === 'maison' ? INDICATOR_SUMMARY_ORDER.slice(0, 2) : INDICATOR_SUMMARY_ORDER.slice(2, 4);
	const block = commune.indicateurs_dvf;
	const fieldIdBloc = `dvf.indicateurs.${type}.mediane_courante`;
	if (block && estBlocAbsent(block)) {
		const etat = absence(block.etat, block.motif);
		return {
			lignes: [ligne({ id: idMediane, fieldId: fieldIdBloc, libelle: null, etat, effectif: null, fenetre: null }, null)],
			provenance: SANS_PROVENANCE, methode: null, limites: null, periode: null
		};
	}
	if (block) {
		const stats = statsDvf(block, type).courante;
		const lue = stats.mediane.etat === 'value'
			? valeur(fieldIdBloc, stats.mediane.valeur)
			: absence(stats.mediane.etat, stats.mediane.motif, stats.effectif);
		// No sale in the window: the panel says why there is no median (03-17 F56); the served state
		// is unchanged.
		const etat: EtatKpi = lue.kind === 'absence' && stats.effectif === 0
			? { ...lue, aide: AUCUNE_VENTE }
			: lue;
		const fenetre = fenetreServie(stats);
		return {
			lignes: [ligne({
				id: idMediane, fieldId: fieldIdBloc, libelle: null, etat, fenetre: fenetreCourte(stats),
				effectif: stats.effectif == null ? null : ventes('dvf.indicateurs.effectif', stats.effectif)
			}, stats.mediane.etat === 'value' ? stats.mediane.valeur : null)],
			provenance: provenanceBloc(block), methode: METHODE_PRIX, limites: limitesDvf(block), periode: fenetre
		};
	}
	const colonne: ColonnePlate = type === 'maison' ? 'prix_m2_median_maison' : 'prix_m2_median_appartement';
	const colonneEffectif: ColonnePlate = type === 'maison' ? 'nb_mutations_12m_maison' : 'nb_mutations_12m_appartement';
	const fieldId = `dvf.${colonne}`;
	const effectifLu = etatDeColonne(commune, colonneEffectif);
	const n = effectifLu.kind === 'valeur' ? Number(effectifLu.valeur) : null;
	const etat = etatColonne(commune, colonne, fieldId, n);
	// Preserve the inclusive window served by the API; the latest import does not date a median. The
	// card spells it (N-2), the panel keeps it as served.
	const fenetre = etat.kind === 'absence' && etat.etat === 'out_of_coverage'
		? null
		: commune.fenetre_mediane_dvf ?? 'Période de mesure non renseignée';
	return {
		// The label of the theme row of a flat median, the arbitrated one: « hors ventes sur plan » is kept.
		libelle: type === 'maison' ? COPIE_DVF.lignesCommune.prixMedianMaison : COPIE_DVF.lignesCommune.prixMedianAppartement,
		lignes: [ligne({
			id: colonne, fieldId, libelle: null, etat, fenetre: fenetre && periodeEnLettres(fenetre),
			effectif: n === null ? null : ventes(`dvf.${colonneEffectif}`, n)
		}, valeurBrute(commune, colonne))],
		provenance: provenanceColonne(sources, 'dvf'), methode: null, limites: null, periode: fenetre
	};
}

// --- ③ trend ---------------------------------------------------------------------------------

function ligneTendance(block: IndicateursDvf, type: TypeBien): KpiLigne {
	const id = type === 'maison' ? INDICATOR_SUMMARY_ORDER[4] : INDICATOR_SUMMARY_ORDER[5];
	const fieldId = `dvf.indicateurs.${type}.variation_pct`;
	const classe = statsDvf(block, type);
	// A4 (04.7-15): a served value on a begun period is withheld; the card says why on its face.
	const variation: NombreOuAbsence = variationPubliable(block, classe);
	const etat: EtatKpi = variation.etat === 'value'
		? valeur(fieldId, variation.valeur, true)
		: variation.motif === PERIODE_ENTAMEE
			? { kind: 'absence', etat: variation.etat, motif: variation.motif, effectif: classe.courante.effectif, libelle: LIBELLE_PERIODE_ENTAMEE, aide: PERIODE_ENTAMEE }
			: absence(variation.etat, variation.motif, classe.courante.effectif);
	const effectifs = [classe.precedente.effectif, classe.courante.effectif];
	const effectif = effectifs.every((n) => n != null)
		? `${formatBareNumber('dvf.indicateurs.effectif', effectifs[0]!)} puis ${ventes('dvf.indicateurs.effectif', effectifs[1]!)}`
		: null;
	// Under the value, the sizes and the two short periods (« 2024 → 2025 », 03-17 R-1 option (b));
	// the two exact windows are read in the panel (« Période »). The arrow never starts a line.
	const periodes = [classe.precedente.fenetre, classe.courante.fenetre];
	return ligne({
		id, fieldId, libelle: SOUS_LIBELLES[type], libelleChamp: LIBELLES_TENDANCE[type], etat, effectif,
		fenetre: periodes[0] && periodes[1] ? `${periodeCourte(periodes[0])} → ${periodeCourte(periodes[1])}` : null,
		periodeServie: [fenetreServie(classe.precedente), fenetreServie(classe.courante)].filter(Boolean).join(' / ') || null,
		qualification: classe.qualification_tendance ?? null
	}, variation.etat === 'value' ? variation.valeur : null);
}

/**
 * The yearly series (decision of 2026-11-11): the most sold type first, its served change or the
 * state that withholds it, its curve and its period on the card; the medians and counts of every
 * year in the panel.
 */
function lireTendanceAnnuelle(commune: CommuneSynthese, sources: Sources): Lecture | null {
	const tendance = tendanceServie(commune);
	if (!tendance) return null;
	const periode = periodeTendance(tendance);
	const types = typesDeLaTuile(tendance);
	const lignes = types.map((type) => {
		const etat = etatTendance(tendance, type);
		return ligne({
			id: `dvf-tendance-${type.type_bien}`, fieldId: CHAMP_EVOLUTION, libelle: titreDuType(type),
			libelleChamp: LIBELLES_TENDANCE[type.type_bien], etat, effectif: null, fenetre: null,
			serie: pointsTendance(type), periodeVisible: type.etat === 'echantillon_insuffisant' ? undefined : periode,
			// A type without two medians has nothing to show: its state is read on its theme row (4.9).
			enSynthese: type.etat !== 'echantillon_insuffisant'
		}, type.evolution_pct);
	});
	return {
		definition: DEFINITION_TENDANCE_ANNUELLE, objet: OBJET_TENDANCE_ANNUELLE, detail: entreesDetail(tendance.types),
		lignes, provenance: provenanceColonne(sources, 'dvf'), methode: methodeTendance(tendance),
		limites: LIMITES_TENDANCE_ANNUELLE, periode: `${periode}, années civiles entières`
	};
}

function lireTendance(commune: CommuneSynthese, sources: Sources): Lecture {
	const annuelle = lireTendanceAnnuelle(commune, sources);
	if (annuelle) return annuelle;
	const block = commune.indicateurs_dvf;
	const types: TypeBien[] = ['maison', 'appartement'];
	if (block && !estBlocAbsent(block)) {
		const lignes = types.map((type) => ligneTendance(block, type));
		return {
			lignes, provenance: provenanceBloc(block), methode: METHODE_TENDANCE, limites: limitesDvf(block),
			// The panel keeps the exact served windows (« … (fin exclue) »), the card the short ones.
			periode: types
				.map((type) => [fenetreServie(statsDvf(block, type).precedente), fenetreServie(statsDvf(block, type).courante)].filter(Boolean).join(' / '))
				.find(Boolean) ?? null
		};
	}
	const lignes = types.map((type) => {
		const id = type === 'maison' ? INDICATOR_SUMMARY_ORDER[4] : INDICATOR_SUMMARY_ORDER[5];
		const fieldId = `dvf.indicateurs.${type}.variation_pct`;
		const etat: EtatKpi = block ? absence(block.etat, block.motif) : { kind: 'absence', effectif: null, ...tendanceSansBloc(commune, type) };
		return ligne({ id, fieldId, libelle: SOUS_LIBELLES[type], libelleChamp: LIBELLES_TENDANCE[type], etat, effectif: null, fenetre: null }, null);
	});
	return { lignes, provenance: SANS_PROVENANCE, methode: null, limites: null, periode: null };
}

// --- ④ dominant DPE class --------------------------------------------------------------------

const OBJET_DPE = 'Diagnostics de performance énergétique (documents) de la commune';
const CLASSES_DPE: readonly string[] = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];

function lireDpe(commune: CommuneSynthese, sources: Sources): Lecture {
	const [idMediane, idModes] = [INDICATOR_SUMMARY_ORDER[6], INDICATOR_SUMMARY_ORDER[7]];
	const block = commune.indicateurs_dpe;
	if (block && estBlocAbsent(block)) {
		const etat = absence(block.etat, block.motif);
		return {
			lignes: [ligne({ id: idModes, fieldId: 'dpe.indicateurs.modes', libelle: null, etat, effectif: null, fenetre: null }, null)],
			provenance: SANS_PROVENANCE, methode: null, limites: LIMITES_DPE, periode: null
		};
	}
	if (block) return lireBlocDpe(block, idModes, idMediane);
	const etat = etatColonne(commune, 'dpe_median', 'dpe.dpe_median');
	const classe = etat.kind === 'valeur' && CLASSES_DPE.includes(etat.texte) ? [etat.texte as EnergyClass] : undefined;
	// Only the median is served: the marker says so in its title (03-17 V27, F36), and its line says
	// that no size nor period is published (M-7), a text of absence, not a value.
	return {
		libelle: 'Classe DPE médiane',
		definition: DEFINITIONS.dpeMediane,
		lignes: [ligne({ id: 'dpe_median', fieldId: 'dpe.dpe_median', libelle: null, etat,
			effectif: 'Effectif non renseigné', fenetre: flatCommunePeriod(commune, sources, 'dpe'), classes: classe }, valeurBrute(commune, 'dpe_median'))],
		provenance: provenanceColonne(sources, 'dpe'), methode: null, limites: LIMITES_DPE,
		periode: flatCommunePeriod(commune, sources, 'dpe')
	};
}

function lireBlocDpe(block: IndicateursDpe, idModes: string, idMediane: string): Lecture {
	const provenance = provenanceBloc(block);
	const resultat = block.resultat;
	if (resultat.etat !== 'value') {
		const etat = absence(resultat.etat, resultat.motif);
		return {
			lignes: [ligne({ id: idModes, fieldId: 'dpe.indicateurs.modes', libelle: null, etat, effectif: null, fenetre: null }, null)],
			provenance, methode: null, limites: LIMITES_DPE, periode: `Évaluation : ${block.date_evaluation}`
		};
	}
	// modes [] and mediane_ordinale null are served bare: their cause is the F/G share's state
	// (03-06 review). Only a declared zero_denominator is shown; anything else is undeclared.
	const sansClasse = (fieldId: string): EtatKpi => {
		if (resultat.part_fg_pct.etat === 'zero_denominator') return absence('zero_denominator', resultat.part_fg_pct.motif);
		signalerAbsenceNonDeclaree(fieldId);
		return { kind: 'absence-non-declaree' };
	};
	const bornes = resultat.date_etablissement_min && resultat.date_etablissement_max
		? `${resultat.date_etablissement_min} → ${resultat.date_etablissement_max}`
		: null;
	// The base of the most frequent class is the known classes it is computed on (04.7-15 review),
	// the same count as the distribution chart and the FAQ.
	const connues = resultat.population.classes_connues;
	const effectif = `sur ${formatBareNumber('dpe.indicateurs.population.total', connues)} ${connues === 1 ? 'diagnostic' : 'diagnostics'} de classe connue`;
	const modes = resultat.modes;
	const lignes = [
		ligne({
			id: idModes, fieldId: 'dpe.indicateurs.modes',
			// The title of the tile says it: « Classe DPE la plus fréquente », as its theme row does.
			libelle: null,
			etat: modes.length ? { kind: 'valeur', texte: modes.join(', '), unite: null } : sansClasse('dpe.indicateurs.modes'),
			// The bounds of the establishment dates are read in the panel (« Période », 03-17 R-1).
			effectif, fenetre: null, classes: modes.length ? [...modes] : undefined
		}, modes.length ? modes : null),
		ligne({
			id: idMediane, fieldId: 'dpe.indicateurs.mediane_ordinale', libelle: 'Classe DPE médiane',
			etat: resultat.mediane_ordinale ? { kind: 'valeur', texte: resultat.mediane_ordinale, unite: null } : sansClasse('dpe.indicateurs.mediane_ordinale'),
			effectif: null, fenetre: null, classes: resultat.mediane_ordinale ? [resultat.mediane_ordinale] : undefined
		}, resultat.mediane_ordinale)
	].filter((item) => item.etat.kind !== 'absence-non-declaree');
	return {
		libelle: libelleModesDpe(modes.length),
		lignes, provenance, methode: METHODE_DPE, limites: LIMITES_DPE,
		periode: [`Évaluation : ${block.date_evaluation}`, bornes && `diagnostics établis de ${bornes}`].filter(Boolean).join(' · ')
	};
}

// --- assembly ---

// One sentence each, the sense of the glossary in plain words (≤ 120 characters, 04.7-15 T1).
const DEFINITIONS = {
	prixMaisons: 'Prix au m² qui partage en deux moitiés les ventes de maisons retenues sur la période.',
	prixAppartements: 'Prix au m² qui partage en deux moitiés les ventes d’appartements retenues sur la période.',
	tendance: 'Écart en % entre les prix médians de deux périodes consécutives de douze mois.',
	dpeDominant: 'Classe énergétique la plus fréquente parmi les diagnostics (DPE) dont la classe est connue.',
	dpeMediane: 'Classe énergétique médiane des diagnostics (DPE) de la commune, sur l’échelle de A à G.'
} as const;

function volet(fieldId: string, objet: string, lecture: Lecture): EntreeVolet[] {
	const entrees: EntreeVolet[] = [];
	const ajouter = (terme: string, texte: string | null | undefined) => {
		if (texte) entrees.push({ terme, valeur: texte });
	};
	// Public definition (03-17 F66): never the technical description of the contract.
	ajouter(LIBELLE_EN_CLAIR, enClairChamp(fieldId));
	ajouter('Définition', definitionPublique(fieldId));
	ajouter('Objet', objet);
	ajouter('Période', lecture.periode);
	ajouter('Unité', unite(fieldId));
	ajouter('Source', lecture.provenance.source?.nom);
	ajouter('Dernière importation réussie', lecture.provenance.importation);
	ajouter('Méthode', lecture.methode);
	// Served qualification of the lines (03-17 F28): in the panel, never on the card.
	ajouter('Qualification', [...new Set(lecture.lignes.map((item) => item.qualification).filter(Boolean))].join(' · ') || null);
	ajouter('Effectif', lecture.lignes.map((item) => item.effectif).filter(Boolean).join(' · ') || null);
	const absences = lecture.lignes.flatMap((item) =>
		item.etat.kind === 'absence' ? [{ libelle: item.libelle, etat: item.etat }] : []
	);
	if (absences.length) entrees.push({ terme: 'État et motif', absences });
	entrees.push(...(lecture.detail ?? []));
	ajouter('Limites', lecture.limites);
	return entrees;
}

function kpi(
	id: KpiCommune['id'],
	rang: KpiCommune['rang'],
	libelle: string,
	definition: string,
	objet: string,
	lecture: Lecture
): KpiCommune {
	const lignes = lecture.lignes.filter((item) => item.etat.kind !== 'absence-non-declaree');
	const fieldId = (lignes[0] ?? lecture.lignes[0]).fieldId;
	return { id, rang, libelle: lecture.libelle ?? libelle, definition: lecture.definition ?? definition, lignes, source: lecture.provenance.source, volet: volet(fieldId, lecture.objet ?? objet, { ...lecture, lignes }) };
}

/**
 * The four KPIs of the synthesis, in the validated order (D-46). Uses only the loaded response:
 * no fetch, no aggregation, no comparison.
 */
export function communeKpis(commune: CommuneSynthese, sources: Sources = null): KpiCommune[] {
	return [
		kpi('prix-maisons', 1, LIBELLES_MEDIANE.maison, DEFINITIONS.prixMaisons, OBJETS_PRIX.maison, lirePrix(commune, 'maison', sources)),
		kpi('prix-appartements', 2, LIBELLES_MEDIANE.appartement, DEFINITIONS.prixAppartements, OBJETS_PRIX.appartement, lirePrix(commune, 'appartement', sources)),
		kpi('tendance', 3, LIBELLE_TENDANCE, DEFINITIONS.tendance, OBJET_TENDANCE, lireTendance(commune, sources)),
		kpi('dpe-dominant', 4, libelleModesDpe(1), DEFINITIONS.dpeDominant, OBJET_DPE, lireDpe(commune, sources))
	];
}

/**
 * « L'essentiel » (4.9, lot 3): a synthesis shows what is known. A line without a value is dropped, its
 * state and reason being read on its theme row; a tile without a known line is dropped, the others keep
 * their rank. A kept tile no longer speaks of the dropped lines, in its panel either.
 */
export function reperesConnus(kpis: KpiCommune[]): KpiCommune[] {
	return kpis.flatMap((item) => {
		const lignes = item.lignes.filter((ligneLue) => ligneLue.etat.kind === 'valeur' || ligneLue.enSynthese);
		if (!lignes.length) return [];
		if (lignes.length === item.lignes.length) return [item];
		const effectif = lignes.map((ligneLue) => ligneLue.effectif).filter(Boolean).join(' · ');
		const volet = item.volet.flatMap((entree): EntreeVolet[] => {
			if (entree.terme === 'État et motif') return [];
			if (entree.terme !== 'Effectif') return [entree];
			return effectif ? [{ terme: 'Effectif', valeur: effectif }] : [];
		});
		return [{ ...item, lignes, volet }];
	});
}

export const RESERVES_MAX = 4;

// A caveat of a tile the page does not show points at the theme that holds its rows.
const THEME_DU_REPERE: Readonly<Record<KpiCommune['id'], string>> = {
	'prix-maisons': '#critere-prix_commune',
	'prix-appartements': '#critere-prix_commune',
	tendance: '#critere-prix_commune',
	'dpe-dominant': '#critere-dpe'
};

// A caveat is read by a visitor: the absence itself is already worded by its badge (« État
// source : … », « Motif : … » since 03-17 F57), and a caveat that carries a technical value (a
// state key, a snake_case enum, `unknown`) would put jargon next to the synthesis.
function reserveLisible(texte: string): boolean {
	if (/^(?:État source|Motif(?: source)?)\s*:/.test(texte)) return false;
	return !/\b[a-z]+_[a-z_]+\b|\b(?:unknown|partial|complete)\b/.test(texte);
}

/**
 * « Ce qui mérite une lecture attentive »: the critical caveats the data layer already qualified
 * for the rows behind the four KPIs (`criticalSummary.warnings` of `mapCommuneToGrid`), one per
 * KPI at most, never twice the same text, four at most, each anchored to its KPI, or to its theme when
 * the tile is not shown.
 */
export function reservesSynthese(
	commune: CommuneSynthese,
	sources: Sources = null,
	grids: CommuneGridModel = mapCommuneToGrid(commune, sources)
): ReserveSynthese[] {
	const rows = [grids.synthese, ...Object.values(grids.themes)].flatMap((grid) => grid.rows);
	const vues = new Set<string>();
	const reserves: ReserveSynthese[] = [];
	const kpis = communeKpis(commune, sources);
	const montres = new Set(reperesConnus(kpis).map((item) => item.id));
	for (const item of kpis) {
		const ids = new Set(item.lignes.map((l) => l.id));
		const fields = new Set(item.lignes.map((l) => l.fieldId));
		const avertissements = rows
			.filter((row) => ids.has(row.concept.id.split(':').at(-1) ?? '') || (row.concept.fieldId != null && fields.has(row.concept.fieldId)))
			.flatMap((row) => Object.values(row.cells).flatMap((cell) => cell?.criticalSummary?.warnings ?? []));
		const texte = avertissements.find((warning) => warning.trim() && reserveLisible(warning) && !vues.has(warning));
		if (!texte) continue;
		vues.add(texte);
		reserves.push({ kpi: item.id, ancre: montres.has(item.id) ? `#kpi-${item.id}` : THEME_DU_REPERE[item.id], texte });
		if (reserves.length === RESERVES_MAX) break;
	}
	return reserves;
}
