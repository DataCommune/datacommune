import type {
	BlocIndicateur,
	ClasseDvf,
	CommuneSynthese,
	IndicateurAbsent,
	IndicateursDvf,
	NombreOuAbsence,
	ProvenanceIndicateur,
	QuantilesOuAbsence,
	SourceAttribution,
	StatistiquesFenetreDvf
} from '$lib/api/commune';
import type { FieldPresentation, KvRow, QualifiedAbsenceReason } from '$lib/types/fiche';
import { etatDeColonne } from './etats-colonnes';
import { libelleEtat, libelleMotifAbsence } from './etats-libelles';

export interface CommuneIndicatorRows {
	marche: KvRow[];
	energie: KvRow[];
}

export const DPE_DATES_NOT_REPORTED = 'Dates des diagnostics non renseignées';

/** A historical row's served period or explicit unknown, never its import timestamp. */
export function flatCommunePeriod(
	commune: CommuneSynthese,
	sources: SourceAttribution[] | null | undefined,
	dataset: string | null,
	vintage: string | null = null,
	fieldId: string | null = null
): string {
	const period = sources?.find(source => source.dataset === dataset)?.periode;
	const label = period?.statut === 'connue' ? period.label?.trim() : null;
	if (dataset === 'dvf') return commune.fenetre_mediane_dvf ?? 'Période de mesure non renseignée';
	// A dataset publication does not bound the establishment dates of its documents.
	if (dataset === 'dpe') return DPE_DATES_NOT_REPORTED;
	if (dataset === 'vacance') {
		const vacantYear = commune.annee_vacance == null ? 'année non renseignée' : String(commune.annee_vacance);
		if (fieldId === 'vacance.nb_vacants_plus_2ans') return `Vacants : ${vacantYear}`;
		// Only a proven pair qualifies both vintages. Formatting removes column jargon, not facts.
		const proof = label?.match(/(?:^|; )(Logements vacants : \d{4} via pp_vacant_\d{2} ; parc privé : \d{4} via (?:ff_)?pp_total_\d{2})$/)?.[1];
		if (proof) return proof.replace(/ via (?:pp_vacant|(?:ff_)?pp_total)_\d{2}/g, '');
		return `Vacants : ${vacantYear} · année du parc privé non renseignée`;
	}
	if (fieldId === 'loyers.annee' && commune.annee_loyers != null) return `Millésime ${commune.annee_loyers}`;
	if (vintage) return `Millésime ${vintage}`;
	if (label) {
		const quarter = label.match(/^(\d{4})_T([1-4])$/);
		return `Période source : ${quarter ? `T${quarter[2]} ${quarter[1]}` : label}`;
	}
	if (period?.statut === 'connue' && period.debut && period.fin) return `Bornes source : ${period.debut} → ${period.fin}`;
	if (dataset === 'scolaire') return 'Période de la sectorisation non renseignée';
	return 'Période source non renseignée';
}

const DATE_EN_LETTRES = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });

/** A served ISO day spelled in full (« 26 septembre 2026 », F15); null when it is not a day. */
export function dateEnLettres(iso: string): string | null {
	const date = new Date(`${iso}T00:00:00Z`);
	return /^\d{4}-\d{2}-\d{2}$/.test(iso) && !Number.isNaN(date.getTime()) ? jourEnLettres(date) : null;
}

/** A day spelled in full; the first of a month is an ordinal in French (« 1er juillet 2021 »). */
export function jourEnLettres(date: Date): string {
	return DATE_EN_LETTRES.format(date).replace(/^1 /, '1er ');
}

/** Reason of a variation withheld because its current window is not closed (04.7-15, Origami A4). */
export const PERIODE_ENTAMEE =
	'La période la plus récente n’est pas close dans la livraison DVF reçue : aucune variation n’est publiée sur une période entamée.';

/**
 * The served variation of a class, or `not_qualified` with its reason when the current window ends
 * after the received DVF delivery (A4: no variation on a begun period). Only a served value is
 * withheld: a served absence keeps its own state and reason. Read by the marker ③, the theme rows
 * and the comparison criteria alike.
 */
export function variationPubliable(block: IndicateursDvf, classe: ClasseDvf): NombreOuAbsence {
	const ancrage = block.ancrage_temporel;
	const fin = classe.courante.fenetre?.fin_exclue;
	const entamee = ancrage.etat === 'delivery_received_and_stock_bound' && fin != null && fin > ancrage.periode_fin_exclue;
	return entamee && classe.variation_pct.etat === 'value' ? { etat: 'not_qualified', motif: PERIODE_ENTAMEE } : classe.variation_pct;
}

/** Label of a variation withheld on a begun period; the state stays the served one. */
export const LIBELLE_PERIODE_ENTAMEE = 'Période non close';

/** Help of a median absent because the window holds no sale (03-17 F56). */
export const AUCUNE_VENTE = 'Aucune vente sur la période : pas de prix médian.';

/** Reason of the trend fallback when the site serves no dated DVF block. */
export const TENDANCE_NON_PUBLIEE = "Ce site ne publie pas l'évolution des prix pour cette commune.";

// One field, one label (4.9, lot 3): the tile and the theme row read these words.
export const LIBELLE_TENDANCE = 'Tendance du prix médian';
export const LIBELLES_TENDANCE = { maison: `${LIBELLE_TENDANCE} des maisons`, appartement: `${LIBELLE_TENDANCE} des appartements` } as const;
export const LIBELLES_MEDIANE = { maison: 'Prix médian des maisons', appartement: 'Prix médian des appartements' } as const;

/** Label of the most frequent DPE class, ex aequo included, on the tile and on the theme row. */
export function libelleModesDpe(nombre: number): string {
	return nombre > 1 ? 'Classes les plus fréquentes, ex aequo' : 'Classe DPE la plus fréquente';
}

// Explanations of the rows behind the tiles: a dropped tile leaves them on its theme row (4.9, lot 3).
export const OBJET_TENDANCE =
	'Évolution du prix médian des ventes de maisons, puis d’appartements, entre deux fenêtres annuelles';
export const METHODE_PRIX =
	'Médiane des prix au mètre carré des mutations résidentielles mono-local, hors ventes sur plan, situées dans une seule commune.';
export const METHODE_TENDANCE = 'Variation servie par l’API : 100 × (médiane courante ÷ médiane précédente − 1).';
export const METHODE_DPE =
	'Classes de fréquence maximale parmi les diagnostics dont la classe est connue, toutes retenues à égalité, dans l’ordre A à G ; médiane ordinale (centile discret sur l’ordre A à G).';
export const LIMITES_DPE = 'Un DPE est un document, pas un logement : ces diagnostics ne représentent pas un décompte de logements.';

export function limitesDvf(block: IndicateursDvf): string {
	return block.ancrage_temporel.etat === 'unknown'
		? block.ancrage_temporel.motif
		: 'Révisions de la source possibles ; exhaustivité historique inconnue.';
}

export interface TendanceAbsente {
	etat: 'out_of_coverage' | 'not_available';
	motif: string;
	/** Written by the interface: the glossary sentence of `not_available` would blame DVF. */
	aide?: string;
}

/**
 * The trend of a commune without a dated DVF block. The flat columns carry no trend: the same source
 * out of coverage is said as such, otherwise this site does not publish the dated trend.
 * TRANSITIONAL: the API does not type a null block. This interface fallback goes when it does
 * (follow-up for 03-09).
 */
export function tendanceSansBloc(commune: CommuneSynthese, type: 'maison' | 'appartement'): TendanceAbsente {
	const prix = etatDeColonne(commune, type === 'maison' ? 'prix_m2_median_maison' : 'prix_m2_median_appartement');
	return prix.kind === 'absence' && prix.etat === 'out_of_coverage'
		? { etat: 'out_of_coverage', motif: prix.motif }
		: { etat: 'not_available', motif: TENDANCE_NON_PUBLIEE, aide: TENDANCE_NON_PUBLIEE };
}

/** Served bounds of the DPE establishment dates, spelled (04.7-15); an unknown bound is said. */
function bornesDpe(min: string | null, max: string | null): string {
	const [debut, fin] = [min && (dateEnLettres(min) ?? min), max && (dateEnLettres(max) ?? max)];
	return debut && fin ? `du ${debut} au ${fin}` : `${debut ?? 'début inconnu'} → ${fin ?? 'fin inconnue'}`;
}

const integer = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });
const price = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });

function sourceLabel(provenance: ProvenanceIndicateur[]): string {
	return provenance.map((item) => item.nom).filter(Boolean).join(', ') || 'Source non renseignée';
}

function provenanceReserves(provenance: ProvenanceIndicateur[]): string[] {
	return provenance.flatMap((item) => [
		`Source : ${item.nom}`,
		`Producteur : ${item.producteur}`,
		`Licence : ${item.licence}`,
		`Référence : ${item.reference}`,
		`Publication source : ${item.date_publication_source ?? 'non renseignée'}`,
		`Snapshot : ${item.date_snapshot ?? 'non renseigné'}`,
		`Import : ${item.date_import ?? 'non renseigné'}`,
		`Couverture : ${item.couverture}`
	]);
}

function blockReserves(block: BlocIndicateur): string[] {
	return [
		`Calcul : ${block.date_calcul}`,
		`Génération : ${block.generation ?? 'non renseignée'}`,
		`Population : ${block.version_population}`,
		`Source(s) : ${sourceLabel(block.provenance)}`,
		...provenanceReserves(block.provenance)
	];
}

function reason(absence: IndicateurAbsent): QualifiedAbsenceReason {
	const label = absenceLabel(absence);
	return {
		code: absence.etat === 'zero_denominator' ? 'zero_denominator' : absence.etat === 'suppressed' ? 'suppressed' : absence.etat === 'insufficient_sample' ? 'insufficient_sample' : 'source_reason',
		label,
		explanation: label,
		evidence: 'Motif transmis par le contrat Commune.'
	};
}

// A served reason is worded by the glossary table, or kept when it is already a sentence; a
// technical key is never displayed (03-17 F57, F58): the label of the served state stands in.
function absenceLabel(absence: IndicateurAbsent): string {
	return libelleMotifAbsence(absence.motif) ?? libelleEtat(absence.etat, absence.motif);
}

function motifReserves(motif: string): string[] {
	const libelle = libelleMotifAbsence(motif);
	return libelle ? [`Motif : ${libelle}`] : [];
}

function uiState(etat: IndicateurAbsent['etat']): 'not_reported' | 'out_of_coverage' | 'not_applicable' {
	if (etat === 'out_of_coverage') return 'out_of_coverage';
	if (etat === 'zero_denominator') return 'not_applicable';
	return 'not_reported';
}

function presentation(value: NombreOuAbsence, reserves: string[]): FieldPresentation {
	if (value.etat === 'value') return { state: 'value', measuredZero: value.valeur === 0, grain: 'Commune', reserves };
	return { state: uiState(value.etat), reason: reason(value), grain: 'Commune', reserves: [
		`État source : ${value.etat}`, ...motifReserves(value.motif), ...reserves
	] };
}

function period(stats: StatistiquesFenetreDvf): string {
	return stats.fenetre
		? `${stats.fenetre.debut_inclus} → ${stats.fenetre.fin_exclue} (fin exclue)`
		: stats.motif_periode ?? 'Période non renseignée';
}

function statsReserves(block: BlocIndicateur, stats: StatistiquesFenetreDvf): string[] {
	return [...blockReserves(block), `Période : ${period(stats)}`, stats.effectif == null ? 'Effectif non renseigné' : `${integer.format(stats.effectif)} mutations`];
}

function numericRow(id: string, fieldId: string, label: string, value: NombreOuAbsence, suffix: string, reserves: string[]): KvRow {
	if (value.etat !== 'value') return { ...absenceRow(id, label, value, reserves), fieldId };
	return {
		id,
		fieldId,
		label,
		value: `${decimal.format(value.valeur)}${suffix}`,
		available: true,
		state: 'value',
		presentation: presentation(value, reserves)
	};
}

function notReportedRow(id: string, label: string, motif: string, reserves: string[]): KvRow {
	return absenceRow(id, label, { etat: 'not_reported', motif }, reserves);
}

function quantileRow(id: string, label: string, value: QuantilesOuAbsence, reserves: string[]): KvRow {
	if (value.etat !== 'value') return absenceRow(id, label, value, reserves);
	const display = value.p10 === value.p90
		? `${price.format(value.p10)} €/m² (P10 = P90)`
		: `P10 ${price.format(value.p10)} · médiane ${price.format(value.p50)} · P90 ${price.format(value.p90)} €/m²`;
	return textValueRow(id, label, display, reserves);
}

function absenceRow(id: string, label: string, value: IndicateurAbsent, reserves: string[]): KvRow {
	return {
		id,
		label,
		value: absenceLabel(value),
		available: false,
		state: uiState(value.etat),
		presentation: { state: uiState(value.etat), reason: reason(value), grain: 'Commune', reserves: [`État source : ${value.etat}`, ...motifReserves(value.motif), ...reserves] }
	};
}

function textRow(id: string, label: string, value: string, reserves: string[]): KvRow {
	return { id, kind: 'context', label, value, state: 'value', presentation: { state: 'value', grain: 'Commune', reserves } };
}

function textValueRow(id: string, label: string, value: string, reserves: string[]): KvRow {
	return { id, label, value, state: 'value', presentation: { state: 'value', grain: 'Commune', reserves } };
}

/**
 * `valuePath` of a row: the dotted path of its value in the /v1/commune row, the key of the served
 * comparison criterion (D-48, D-51). A dispersion row points at its quantiles object, whose three
 * criteria (`.p10`, `.p50`, `.p90`) sit under that path.
 */
function at(valuePath: string, row: KvRow): KvRow {
	return { ...row, valuePath };
}

function dvfClassRows(kind: 'maison' | 'appartement', label: string, block: IndicateursDvf): KvRow[] {
	const data = kind === 'maison' ? block.residentiel_maison : block.residentiel_appartement;
	const current = statsReserves(block, data.courante);
	const previous = statsReserves(block, data.precedente);
	const path = `indicateurs_dvf.residentiel_${kind}`;
	return [
		at(`${path}.courante.mediane`, numericRow(`dvf-${kind}-mediane-courante`, `dvf.indicateurs.${kind}.mediane_courante`, `Prix médian ${label} (fenêtre courante)`, data.courante.mediane, ' €/m²', current)),
		at(`${path}.courante.effectif`, data.courante.effectif == null ? notReportedRow(`dvf-${kind}-effectif-courant`, `Volume ${label} (fenêtre courante)`, 'Effectif non renseigné', current) : textValueRow(`dvf-${kind}-effectif-courant`, `Volume ${label} (fenêtre courante)`, integer.format(data.courante.effectif), current)),
		at(`${path}.courante.quantiles`, quantileRow(`dvf-${kind}-quantiles-courants`, `Dispersion ${label} (fenêtre courante)`, data.courante.quantiles, current)),
		at(`${path}.precedente.mediane`, numericRow(`dvf-${kind}-mediane-precedente`, `dvf.indicateurs.${kind}.mediane_precedente`, `Prix médian ${label} (fenêtre précédente)`, data.precedente.mediane, ' €/m²', previous)),
		at(`${path}.precedente.effectif`, data.precedente.effectif == null ? notReportedRow(`dvf-${kind}-effectif-precedent`, `Volume ${label} (fenêtre précédente)`, 'Effectif non renseigné', previous) : textValueRow(`dvf-${kind}-effectif-precedent`, `Volume ${label} (fenêtre précédente)`, integer.format(data.precedente.effectif), previous)),
		at(`${path}.variation_pct`, numericRow(`dvf-${kind}-variation`, `dvf.indicateurs.${kind}.variation_pct`, `Variation du prix médian ${label}`, variationPubliable(block, data), ' %', [...current, ...previous.map((item) => `Fenêtre précédente — ${item}`), ...(data.qualification_tendance ? [`Qualification : ${data.qualification_tendance}`] : [])]))
	];
}

function receivedAnchor(anchor: Extract<IndicateursDvf['ancrage_temporel'], { etat: 'delivery_received_and_stock_bound' }>): string {
	return [`Lot ${anchor.lot_id}`, `${anchor.periode_debut_inclus} → ${anchor.periode_fin_exclue} (fin exclue)`, anchor.revisions_possibles ? 'révisions possibles' : 'révisions non signalées', anchor.exhaustivite_historique === 'unknown' ? 'exhaustivité historique inconnue' : anchor.exhaustivite_historique, `Observation : ${anchor.date_observation}`].join(' · ');
}

function isAbsent(value: unknown): value is IndicateurAbsent {
	return typeof value === 'object' && value !== null && 'etat' in value && !('version_contrat' in value);
}

const DPE_AXIS_LABELS: Readonly<Record<string, string>> = {
	maison: 'maison', appartement: 'appartement', autre: 'autre', inconnu: 'inconnu',
	unknown: 'inconnue', expired: 'expirée', not_expired_by_date: 'non expirée à la date d’évaluation',
	inconsistent_dates: 'dates incohérentes', future_establishment: 'date d’établissement future',
	not_checked: 'non vérifié', no_successor_known: 'aucun successeur connu', replaced: 'remplacé', ambiguous: 'ambigu',
	existant: 'existant', neuf: 'neuf'
};

function dpeAxis(prefix: string, key: string, count: number): string {
	const label = DPE_AXIS_LABELS[key] ?? key;
	const formatted = integer.format(count);
	return label === key
		? `${prefix} ${label} : ${formatted}`
		: `${prefix} — ${label} [${prefix} ${key} : ${formatted}] : ${formatted}`;
}

/** Pure projection of additive backend blocks. Missing blocks preserve the historical fiche. */
export function mapCommuneIndicators(commune: CommuneSynthese): CommuneIndicatorRows {
	const marche: KvRow[] = [];
	const energie: KvRow[] = [];
	const dvf = commune.indicateurs_dvf;
	if (dvf && isAbsent(dvf)) marche.push(at('indicateurs_dvf', absenceRow('dvf-indicateurs-absence', 'Indicateurs de prix datés', dvf, ['Grain : commune'])));
	else if (dvf) {
		const population = 'Population statistique : mutations résidentielles mono-local hors ventes sur plan mono-commune';
		marche.push(...dvfClassRows('maison', 'des maisons', dvf), ...dvfClassRows('appartement', 'des appartements', dvf));
		for (const row of marche) if (row.presentation?.reserves) row.presentation = { ...row.presentation, reserves: [population, ...row.presentation.reserves] };
		marche.push(textRow('dvf-ancrage', 'Ancrage temporel DVF', dvf.ancrage_temporel.etat === 'unknown' ? dvf.ancrage_temporel.motif : receivedAnchor(dvf.ancrage_temporel), [population, ...blockReserves(dvf)]));
	}
	const dpe = commune.indicateurs_dpe;
	if (dpe && isAbsent(dpe)) energie.push(at('indicateurs_dpe', absenceRow('dpe-indicateurs-absence', 'Indicateurs DPE datés', dpe, ['Grain : commune'])));
	else if (dpe) {
		const reserves = [...blockReserves(dpe), `Évaluation : ${dpe.date_evaluation}`, `Population : ${dpe.population}`];
		if (dpe.resultat.etat !== 'value') energie.push(at('indicateurs_dpe.resultat', absenceRow('dpe-resultat-absence', 'Diagnostics disponibles', dpe.resultat, reserves)));
		else {
			const result = dpe.resultat;
			const populationReserves = [...reserves,
				`${integer.format(result.population.total)} diagnostics disponibles`, `${integer.format(result.population.classes_connues)} classes connues`, `${integer.format(result.distribution.inconnue)} classe inconnue`,
				...Object.entries(result.population.types_batiment).map(([key, count]) => dpeAxis('Type', key, count)),
				...Object.entries(result.population.validite).map(([key, count]) => dpeAxis('Validité', key, count)),
				...Object.entries(result.population.remplacement).map(([key, count]) => dpeAxis('Remplacement', key, count)),
				...Object.entries(result.population.regimes).map(([key, count]) => dpeAxis('Régime', key, count)),
				`Source inconnue : ${integer.format(result.population.source_inconnue)}`,
				`Révisions non vérifiées : ${integer.format(result.population.revision_non_verifiee)}`,
				`Dates d’établissement inconnues : ${integer.format(result.dates_etablissement_inconnues)}`,
				'Traces retirées exclues de la population.', 'Ces diagnostics ne représentent pas un décompte de logements.'
			];
			const distribution = Object.entries(result.distribution.classes).map(([classe, count]) => `${classe} : ${integer.format(count)}`).join(' · ');
			energie.push(
				textRow('dpe-population', 'Population DPE observée', `${integer.format(result.population.total)} diagnostics disponibles`, populationReserves),
				textRow('dpe-distribution', 'Distribution des classes DPE', `${distribution} · inconnue : ${integer.format(result.distribution.inconnue)}`, populationReserves),
				textRow('dpe-periode', 'Bornes des dates d’établissement', bornesDpe(result.date_etablissement_min, result.date_etablissement_max), populationReserves),
				at('indicateurs_dpe.resultat.mediane_ordinale', result.mediane_ordinale == null ? notReportedRow('dpe-mediane', 'Classe DPE médiane', 'Médiane non renseignée', populationReserves) : textValueRow('dpe-mediane', 'Classe DPE médiane', result.mediane_ordinale, populationReserves)),
				at('indicateurs_dpe.resultat.modes', result.modes.length === 0 ? notReportedRow('dpe-modes', 'Classe DPE la plus fréquente', 'Mode non renseigné', populationReserves) : textValueRow('dpe-modes', libelleModesDpe(result.modes.length), result.modes.join(', '), populationReserves)),
				at('indicateurs_dpe.resultat.part_fg_pct', numericRow('dpe-part-fg', 'dpe.indicateurs.part_fg_pct', 'Part des classes F et G', result.part_fg_pct, ' %', populationReserves))
			);
		}
	}
	return { marche, energie };
}
