// Honest API → Fiche model mapper for the Commune fiche.
// Pure module — zero Svelte imports. Positionings are computed front-side, deterministically,
// against PUBLISHED national referentials (Phase 49 D-01..D-07, `./referentiels`). No backend,
// no ML, and — since Phase 49 — no in-house cut-off: the old local verdict-threshold table
// (D9-08/09) judged values against figures no publication backed. A KPI now either cites an
// openable reference or shows no comparison at all (D-07 honest silence).
// (The token itself is deliberately not spelled out here: the acceptance grep for the removed
// table must stay exact, same lesson as 49-01.)
//
// DEGRADED-MODE NOTE (2026-06-01): the frozen KPI set was decided while api.datacommune.fr
// returned HTTP 530, so live values are "à confirmer quand l'API sera up". The mapping logic
// is API-down-agnostic: it degrades per-field (null → 'n.d.' + info) and per-theme (structural
// gap → available:false), never inventing a value or substituting one field for another (D9-01).
//
// PHASE 43 (D-05): number/date formatting and unit suffixes are no longer duplicated here —
// every row is built via `formatField`/`formatNullableField` from `./formatters`, driven by the
// versioned semantic-contract registry (`fiche-fields.v1.json`). A field_id referenced below
// that is not in the registry throws loud at call time (T-43-07), instead of silently rendering
// an unverified value.

import type { CommuneSynthese } from '$lib/api/commune';
import type { KpiItem, KvRow, ThemeModel } from '$lib/types/fiche';
import { COPIE_DVF } from './dvf-attribution-view';
import { formatBareNumber, formatField, formatNullableField } from './formatters';
import { comparerAuReferentiel } from './referentiels';
import { contexteLoyer } from './loyers-prediction';
import { mapCommuneIndicators } from './commune-indicators';

const NA = 'n.d.';

/**
 * A value row of the flat column `column` of /v1/commune: `valuePath` names it, the key of the
 * served comparison criterion (D-48, D-51). The value itself is never read from the path here.
 */
function colonne(column: keyof CommuneSynthese & string, row: KvRow): KvRow {
	return { ...row, valuePath: column };
}

// --- L0 KPI bare-number formatting ---
// KpiItem carries its own separate `unit` slot (rendered by the component), unlike KvRow's
// inline-unit convention — so the KPI builders reuse `formatBareNumber` (registry precision,
// no unit suffix) instead of `formatField` (which would embed the suffix twice).

function fmtBareNumber(fieldId: string, v: number | null): string | number {
	return v == null ? NA : formatBareNumber(fieldId, v);
}

// --- L0 synthese ---
//
// The single comparison authority is `comparerAuReferentiel` (D-01). It returns `null` FAR more
// often than it returns a comparison — a null value, a D-07 registered metric, a field with no
// published referential and an unreadable value all yield nothing. `null` → verdict 'info' and NO
// `verdictLabel`: the chip carries no wording at all rather than an unjustified one. A comparison
// is the justified exception, never the default.

function buildKpi(c: CommuneSynthese): KpiItem[] {
	// 1. Predicted asking rent — D-07 registered in SANS_REFERENTIEL: no published national
	// per-m² rent benchmark exists, and a rent is NEVER a sale price. Always `info`, never a
	// verdictLabel. Per-field policy: do NOT silently substitute loyer_m2_maison for the L0 figure;
	// null → 'n.d.'. The maison loyer remains a distinct, explicit row in the Marché theme.
	const loyerKpi: KpiItem = {
		label: "Loyer d'annonce prédit (appartement)",
		value: fmtBareNumber('loyers.loyer_m2_appartement', c.loyer_m2_appartement),
		unit: c.loyers_appartement?.charges_comprises === true ? '€/m²/mois CC' : '€/m²/mois',
		verdict: 'info',
		fieldId: 'loyers.loyer_m2_appartement',
		presentation: formatNullableField('loyers.loyer_m2_appartement', '', c.loyer_m2_appartement).presentation,
		primary: true
	};

	// 2. Tension de marché — positioned on the official A/B/C classification itself.
	const tension = comparerAuReferentiel('zonage_abc.zone', c.zone_abc);
	const tensionKpi: KpiItem = {
		label: 'Tension de marché',
		value: c.zone_abc ?? NA,
		verdict: tension?.verdict ?? 'info',
		verdictLabel: tension?.label,
		ecartTexte: tension?.ecartTexte,
		fieldId: 'zonage_abc.zone',
		presentation: formatNullableField('zonage_abc.zone', '', c.zone_abc).presentation,
		primary: true
	};

	// 3. Taux de vacance des logements — D-07: the most widely published national LOVAC figure
	// only measures STRUCTURAL vacancy (over 2 years), not the overall rate shown here. Comparing
	// them would be a definition mismatch, so no comparison is shown at all.
	const vacanceKpi: KpiItem = {
		label: 'Taux de vacance',
		value: fmtBareNumber('vacance.taux_vacance', c.taux_vacance),
		unit: '%',
		verdict: 'info',
		fieldId: 'vacance.taux_vacance',
		presentation: formatNullableField('vacance.taux_vacance', '', c.taux_vacance).presentation
	};

	// 4. Declared income per consumption unit, same vintage and metropolitan scope.
	const revenu = comparerAuReferentiel('revenus.revenu_median', c.revenu_median, { annee: c.annee_revenus, codeInsee: c.code_insee });
	const revenuKpi: KpiItem = {
		label: 'Revenu déclaré médian',
		value: fmtBareNumber('revenus.revenu_median', c.revenu_median),
		unit: '€/UC/an',
		verdict: revenu?.verdict ?? 'info',
		verdictLabel: revenu?.label,
		ecartTexte: revenu?.ecartTexte,
		fieldId: 'revenus.revenu_median',
		presentation: formatNullableField('revenus.revenu_median', '', c.revenu_median).presentation
	};

	return [loyerKpi, tensionKpi, vacanceKpi, revenuKpi];
}

// --- L1/L2 themes (5, always present) ---

function buildThemes(c: CommuneSynthese): ThemeModel[] {
	const indicators = mapCommuneIndicators(c);
	// 1. Marché & prix — loyers + zone_abc proxy + DVF commune aggregates (prix médian, volume).
	const marche: ThemeModel = {
		key: 'marche',
		emoji: '💶',
		name: 'Marché & prix',
		hint: 'Prix DVF, loyers, zonage ABC',
		rows: [
			...indicators.marche,
			// Phase 57 (D-14/D-18) : la médiane globale reste, et la ventilation maison/appartement
			// s'ajoute à côté — une médiane « toutes ventes » mélange deux marchés qui n'ont pas le
			// même prix au mètre carré. Chaque médiane vaut `n.d.` sous le seuil de non-divulgation,
			// tandis que l'effectif reste publié : c'est lui qui dit POURQUOI la médiane manque.
			// La mention « hors ventes sur plan » est portée par le libellé faute d'emplacement de
			// réserve dans la structure actuelle, que D-21 interdit de restructurer ; l'acronyme
			// VEFA est développé dans l'info-bulle de chaque ligne (arbitrage D-19, 2026-08-25).
			colonne('prix_m2_median', formatNullableField('dvf.prix_m2_median', COPIE_DVF.lignesCommune.prixMedian, c.prix_m2_median)),
			colonne('prix_m2_median_maison', formatNullableField('dvf.prix_m2_median_maison', COPIE_DVF.lignesCommune.prixMedianMaison, c.prix_m2_median_maison ?? null)),
			colonne('nb_mutations_12m_maison', formatNullableField('dvf.nb_mutations_12m_maison', COPIE_DVF.lignesCommune.ventesMaison, c.nb_mutations_12m_maison ?? null)),
			colonne('prix_m2_median_appartement', formatNullableField('dvf.prix_m2_median_appartement', COPIE_DVF.lignesCommune.prixMedianAppartement, c.prix_m2_median_appartement ?? null)),
			colonne('nb_mutations_12m_appartement', formatNullableField('dvf.nb_mutations_12m_appartement', COPIE_DVF.lignesCommune.ventesAppartement, c.nb_mutations_12m_appartement ?? null)),
			colonne('nb_mutations_12m', formatNullableField('dvf.nb_mutations_12m', COPIE_DVF.lignesCommune.mutations12m, c.nb_mutations_12m)),
			colonne('loyer_m2_appartement', formatNullableField('loyers.loyer_m2_appartement', "Loyer d'annonce prédit appartement", c.loyer_m2_appartement)),
			...contexteLoyer('appartement', c.loyers_appartement, c.loyer_m2_appartement),
			colonne('loyer_m2_maison', formatNullableField('loyers.loyer_m2_maison', "Loyer d'annonce prédit maison", c.loyer_m2_maison)),
			...contexteLoyer('maison', c.loyers_maison, c.loyer_m2_maison),
			colonne('zone_abc', formatNullableField('zonage_abc.zone', 'Zone ABC (tension)', c.zone_abc)),
			colonne('annee_loyers', formatNullableField('loyers.annee', 'Année de référence (loyers)', c.annee_loyers))
		],
		sources: 'DVF, Carte des loyers (estimations ANIL), Zonage ABC'
	};

	// 2. Énergie & bâti — DPE commune aggregates (passoires, classe médiane, année construction)
	// + recensement logements. "Ancienneté" sourced from DPE annee_construction (BD TOPO has none).
	const energie: ThemeModel = {
		key: 'energie',
		emoji: '⚡',
		name: 'Énergie & bâti',
		hint: 'DPE, logements recensés',
		rows: [
			...indicators.energie,
			colonne('dpe_passoires_pct', formatNullableField('dpe.dpe_passoires_pct', 'Part des classes F et G', c.dpe_passoires_pct)),
			colonne('dpe_median', formatNullableField('dpe.dpe_median', 'Classe DPE médiane', c.dpe_median)),
			colonne('nb_logements', formatNullableField('demographie.nb_logements', 'Logements recensés', c.nb_logements)),
			colonne('nb_logements_vacants_recensement', formatNullableField('demographie.nb_logements_vacants_recensement', 'Logements vacants (recensement)', c.nb_logements_vacants_recensement)),
			colonne('annee_construction_mediane', formatNullableField('dpe.annee_construction_mediane', 'Année de construction (médiane)', c.annee_construction_mediane))
		],
		sources: 'DPE (ADEME), Démographie (logements)'
	};

	// 3. Risques — zone_abc proxy + monuments count. Georisques (risques naturels) stays a
	// structural gap (live proxy, not pre-computed in the matview).
	const risques: ThemeModel = {
		key: 'risques',
		emoji: '⚠️',
		name: 'Risques',
		hint: 'Zonage ABC, monuments',
		rows: [
			colonne('zone_abc', formatNullableField('zonage_abc.zone', 'Zonage ABC', c.zone_abc)),
			colonne('zone_abc_reclassement', formatNullableField('zonage_abc.reclassement', 'Reclassement ABC', c.zone_abc_reclassement)),
			formatField('commune.risques.risques_naturels', 'Risques naturels (inondation, RGA, radon)', {
				state: 'out_of_coverage'
			}),
			colonne('nb_monuments', formatNullableField('monuments.nb_monuments', 'Notices de monuments historiques', c.nb_monuments))
		],
		sources: 'Zonage ABC, Monuments historiques'
	};

	// 4. Environnement — best covered: population, revenus, densité + bonus fields.
	const environnement: ThemeModel = {
		key: 'environnement',
		emoji: '🏘️',
		name: 'Environnement',
		hint: 'Démographie, revenus',
		rows: [
			colonne('population', formatNullableField('demographie.population', 'Population', c.population)),
			// superficie/densité are derived from the administrative geometry backfill
			// (app/demographie/backfill.py); a NULL on a covered 44/85 commune means "not
			// computed yet" (soft gap not_reported), never "out of coverage" (DATA-03, 43-10).
			colonne('densite', formatNullableField('demographie.densite', 'Densité', c.densite, 'not_reported')),
			colonne('superficie', formatNullableField('demographie.superficie', 'Superficie', c.superficie, 'not_reported')),
			colonne('nb_menages', formatNullableField('demographie.nb_menages', 'Nombre de ménages', c.nb_menages)),
			colonne('revenu_median', formatNullableField('revenus.revenu_median', 'Revenu déclaré médian', c.revenu_median)),
			colonne('taux_pauvrete', formatNullableField('revenus.taux_pauvrete', 'Taux de pauvreté', c.taux_pauvrete)),
			colonne('indice_gini', formatNullableField('revenus.indice_gini', 'Gini (revenu déclaré)', c.indice_gini)),
			colonne('part_imposee', formatNullableField('revenus.part_imposee', 'Part des ménages fiscaux imposés', c.part_imposee)),
			colonne('nb_equipements', formatNullableField('equipements.nb_equipements', 'Effectif d’équipements observé', c.nb_equipements)),
			colonne('nb_etablissements_scolaires', formatNullableField('scolaire.nb_etablissements_scolaires', 'Établissements référencés par la carte scolaire', c.nb_etablissements_scolaires)),
			colonne('fibre_pct', formatNullableField('connectivite.fibre_pct', 'Fibre (immeubles observés)', c.fibre_pct)),
			{
				kind: 'context', state: 'value', label: 'Portée des dénombrements',
				value: `${c.annee_equipements == null ? 'Millésime des équipements inconnu' : `Équipements : millésime ${c.annee_equipements}`}. Ces nombres décrivent le stock observé dans les sources disponibles, sans garantie d’exhaustivité. Une donnée absente ne vaut pas zéro.`
			}
		],
		sources: 'Démographie, Revenus (Filosofi), Équipements (BPE), Carte scolaire, Arcep'
	};

	// 5. Propriété — vacance + fiscalité + foncier PM (MAJIC). Copropriété stays a gap
	// (RNIC table loaded nationally but 0 row for many communes — degraded honestly).
	const propriete: ThemeModel = {
		key: 'propriete',
		emoji: '📋',
		name: 'Propriété',
		hint: 'Vacance, fiscalité, foncier PM',
		rows: [
			colonne('taux_vacance', formatNullableField('vacance.taux_vacance', 'Taux de vacance', c.taux_vacance)),
			colonne('nb_vacants_plus_2ans', formatNullableField('vacance.nb_vacants_plus_2ans', 'Logements vacants > 2 ans', c.nb_vacants_plus_2ans)),
			colonne('taux_tfpb', formatNullableField('fiscalite.taux_tfpb', 'Taux TFPB', c.taux_tfpb)),
			colonne('taux_th_rs', formatNullableField('fiscalite.taux_th_rs', "Taux TH résidences secondaires", c.taux_th_rs)),
			colonne('taux_teom', formatNullableField('fiscalite.taux_teom', 'Taux TEOM', c.taux_teom)),
			// D-20 (Phase 50): an EPCI NAME is never "out of coverage" — the registry allows only
			// ["value","not_reported"] for it, unlike the numeric fiscalité indicators above. The
			// implicit out_of_coverage default was a mapper defect, surfaced by the allowed_states
			// guard: a missing label is unreported, not outside the covered perimeter.
			colonne('nom_epci', formatNullableField('fiscalite.nom_epci', 'EPCI', c.nom_epci, 'not_reported')),
			formatField('commune.propriete.coproprietes', 'Copropriétés', { state: 'out_of_coverage' }),
			colonne('nb_proprietaires_pm', formatNullableField('foncier_pm.nb_proprietaires_pm', 'Personnes morales référencées', c.nb_proprietaires_pm)),
			colonne('surface_pm_ha', formatNullableField('foncier_pm.surface_pm_ha', 'Foncier personnes morales', c.surface_pm_ha)),
			{
				kind: 'context', state: 'value', label: 'Portée du foncier observé',
				value: 'Personnes morales et parcelles présentes dans les droits fonciers disponibles, sans garantie d’exhaustivité. Une surface incomplète ou contradictoire reste indisponible.'
			}
		],
		sources: 'Vacance, Fiscalité locale, Foncier PM (MAJIC)'
	};

	return [marche, energie, risques, environnement, propriete];
}

/**
 * Map a CommuneSynthese to the Fiche model: 4 L0 KPI + 5 L1/L2 themes.
 * Honest coverage: structural gaps → available:false rows; data gaps (null) → 'n.d.' / info.
 */
export function mapCommuneToFiche(c: CommuneSynthese): { kpi: KpiItem[]; themes: ThemeModel[] } {
	return {
		kpi: buildKpi(c),
		themes: buildThemes(c)
	};
}

/**
 * Compute the honest "depth" of a fiche: how many indicators are actually filled for THIS
 * commune, and how many distinct source datasets back them (phase « vérité »).
 *
 * - `indicateurs`: count of `rows` (commune scope uses `rows` only; rowsBien/rowsCommune stay
 *   undefined here) genuinely rendered — `available !== false` AND `muted !== true`. Structural
 *   gaps and null degradations are excluded, so the figure reflects real coverage, not the schema.
 * - `sources`: size of the Set of comma-split, trimmed segments across every `theme.sources`.
 *   Exact-string dedup: "Zonage ABC" present in two themes counts once; "Démographie" and
 *   "Démographie (logements)" stay distinct (two distinct dataset usages).
 */
export function ficheDepth(themes: ThemeModel[]): { indicateurs: number; sources: number } {
	let indicateurs = 0;
	const sources = new Set<string>();
	for (const theme of themes) {
		for (const row of theme.rows) {
			if (row.kind !== 'context' && row.available !== false && row.muted !== true) indicateurs++;
		}
		for (const segment of theme.sources.split(',')) {
			const trimmed = segment.trim();
			if (trimmed) sources.add(trimmed);
		}
	}
	return { indicateurs, sources: sources.size };
}
