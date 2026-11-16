// Honest API → Fiche model mapper for the Bien fiche.
// Pure module — zero Svelte imports. Positionings are computed front-side, deterministically,
// against PUBLISHED national referentials (Phase 49 D-01..D-07, `./referentiels`). No backend,
// no ML. Since Phase 49 the local DPE threshold table is gone: the A→G bands were already the
// official ADEME ones, but they lived here as an unsourced copy — the mapper now cites the
// published scale itself, so the reader can open it.
//
// CORE INVARIANT (D-04 / D9-01): degrade per-dataset, never block the whole fiche on one KO
// dataset. Three independent per-dataset states are read via datasetState():
//   - 'error'  → the dataset is listed in multi.erreurs[] (a LIST, not a dict — Pitfall 1)
//   - 'absent' → the dataset key is missing from multi.datasets{} (never requested)
//   - 'empty'  → datasets[name] is null or an empty list (requested, no data)
//   - 'ok'     → a real payload (object | list — heterogeneous shapes, Pitfall 2)
// Any non-'ok' state → available:false rows. A value is NEVER invented or substituted.
//
// PHASE 43 (D-05): number/date formatting and unit suffixes are no longer duplicated here —
// every row is built via `formatField`/`formatNullableField` from `./formatters`, driven by the
// versioned semantic-contract registry (`fiche-fields.v1.json`, `bien.*` namespace). A field_id
// referenced below that is not in the registry throws loud at call time (T-43-07).

// PHASE 51 (D-10/D-18): the DPE is no longer READ from the payload — it is RECEIVED. The
// mapper used to take the first row of the DPE block and render it as « le DPE de ce bien » ;
// that block came from a 500 m proximity search, so on a shared parcelle the neighbour's
// diagnostic could be published under this property's identity. The block is now exact-only
// backend-side, but the frontend still had the LATITUDE to interpret a list — this mapper no
// longer has it. It takes a `DpeFicheSelection`: either the diagnostic the attribution policy
// authorised, or the exact reason there is none. There is no third possibility, and no
// fallback that could reconstruct one.

// PHASE 57 (D-16/D-37) : le PRIX non plus n'est plus LU, il est DÉCIDÉ. Le KPI « Prix au m²
// (dernière vente) » prenait la première ligne du bloc DVF — la transaction la plus récente de la
// PARCELLE — et la rendait comme la dernière vente DU BIEN. Sur une parcelle partagée, ce prix
// pouvait être celui d'un local commercial, d'un immeuble vendu à prix global ou d'un autre
// logement. Le mapper reçoit désormais une `DvfAttributionView`, dérivée de la décision fermée du
// backend : soit une transaction qu'il a le droit de montrer AVEC la mention qui dit qu'elle
// n'est pas attribuée au bien, soit l'absence et sa raison. L'historique de parcelle, lui, reste
// lu depuis la charge utile et chaque transaction y porte son étiquette de classe (D-11/D-17).

import type { ResultatMultiResolution } from '$lib/api/resolve';
import type { CommuneSynthese } from '$lib/api/commune';
import { mapCommuneToFiche } from './commune-fiche';
import type { DpeFicheSelection } from './dpe-attribution-view';
import {
	COPIE_DVF,
	deriveDvfAttributionView,
	etiquetteMutation,
	type DvfAttributionView
} from './dvf-attribution-view';
import type { FieldValue, KpiItem, KvRow, ThemeModel } from '$lib/types/fiche';
import { formatBareNumber, formatField, formatNullableField } from './formatters';
import { comparerAuReferentiel } from './referentiels';

const NA = 'n.d.';

/**
 * Fail-closed default of the third argument: no authorised diagnostic, and the coverage gap as
 * the reason. A caller that forgets to pass the selection renders `n.d.` — honest — instead of
 * silently falling back on a payload read, which is exactly the behaviour this phase removes.
 */
export const SELECTION_DPE_INDISPONIBLE: DpeFicheSelection = {
	diagnostic: null,
	gapState: 'out_of_coverage'
};

/**
 * D-03: the DPE record exists but this source field is null — distinct from "no DPE at
 * all" (the dataset-state gap below). DPE-scoped only.
 */
function kvSourceNull(fieldId: string, label: string): KvRow {
	return formatField(fieldId, label, { state: 'not_reported' });
}

/**
 * Construction age. ADEME fills the exact year (annee_construction) only ~63% of the
 * time but the period bracket (periode_construction, e.g. "1948-1974") ~100%. Prefer the
 * exact year (raw, no thousands separator), fall back to the bracket, else unavailable.
 */
function kvConstruction(annee: number | null, periode: string | null): KvRow {
	if (annee != null) {
		return formatField('bien.dpe.annee_construction', 'Année de construction', {
			state: 'value',
			value: annee
		});
	}
	if (periode != null && periode !== '') {
		return formatField('bien.dpe.periode_construction', 'Année de construction', {
			state: 'value',
			value: periode
		});
	}
	// D-03: record exists (mapDpeRows only calls this in the 'ok' branch) but both source
	// fields are null — "Non renseigné", not the generic record-absent gap.
	return kvSourceNull('bien.dpe.annee_construction', 'Année de construction');
}

// --- per-dataset state (the 3+1 states, Pattern 4 / Pitfall 1) ---

type DatasetState = 'ok' | 'empty' | 'error' | 'absent';

function datasetState(multi: ResultatMultiResolution, name: string): DatasetState {
	const inErreurs = (multi.erreurs ?? []).some((e) => e.dataset === name);
	if (inErreurs) return 'error'; // requested, crashed
	// ADRESSE_INCOMPLETE: the payload exists but is commune-level, not bien-level (Pitfall 3).
	// Treat it as 'empty' for the BIEN scope → its bien rows degrade; the data still surfaces
	// commune-side via mapCommuneToFiche. This keeps the « sur ce bien » block honest.
	if (adresseIncompleteDatasets(multi).has(name)) return 'empty';
	if (!(name in (multi.datasets ?? {}))) return 'absent'; // never requested
	const data = (multi.datasets ?? {})[name];
	if (data == null) return 'empty'; // requested, no data (null)
	if (Array.isArray(data) && data.length === 0) return 'empty';
	return 'ok';
}

/** Datasets that are commune-level (not bien-level) when carrying ADRESSE_INCOMPLETE (Pitfall 3). */
function adresseIncompleteDatasets(multi: ResultatMultiResolution): Set<string> {
	return new Set(
		multi.avertissements.filter((a) => a.code === 'ADRESSE_INCOMPLETE').map((a) => a.dataset)
	);
}

/**
 * D-04 state for a non-'ok' dataset's gap rows: a crashed dataset (`error`) is a
 * `temporary_error` (retry might work); a never-requested or empty dataset is `out_of_coverage`.
 * Both render as a hard gap (available:false, "non disponible") via `formatField` — the same
 * treatment the pre-Phase-43 kvGap() gave every non-'ok' state, preserved exactly.
 */
function datasetGapState(state: Exclude<DatasetState, 'ok'>): FieldValue {
	return state === 'error' ? { state: 'temporary_error' } : { state: 'out_of_coverage' };
}

/**
 * Same D-04 treatment for the DPE, but driven by the ATTRIBUTION instead of the payload state.
 * `temporary_error` means the DPE subsystem failed and a retry may work; anything else is a
 * coverage gap. Keeping the two apart at the row level is what stops an outage from being read
 * as « aucun DPE ici » (D-13) — they render identically, they do not MEAN the same thing.
 */
function dpeGapState(selection: DpeFicheSelection): FieldValue {
	return selection.gapState === 'temporary_error'
		? { state: 'temporary_error' }
		: { state: 'out_of_coverage' };
}

// --- defensive payload readers (heterogeneous shapes, Pitfall 2) ---

function asObject(v: unknown): Record<string, unknown> | null {
	return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function asList(v: unknown): Record<string, unknown>[] {
	return Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
}

function asNumber(v: unknown): number | null {
	if (typeof v === 'number') return Number.isFinite(v) ? v : null;
	// The resolve endpoint serializes Postgres `numeric` columns as JSON strings
	// ("150000.00"). Coerce a numeric string so those fields render instead of
	// falling to "non disponible" (defense-in-depth alongside the backend fix).
	if (typeof v === 'string' && v.trim() !== '') {
		const n = Number(v);
		return Number.isFinite(n) ? n : null;
	}
	return null;
}

function readNumber(obj: Record<string, unknown> | null, key: string): number | null {
	return asNumber(obj?.[key]);
}

function readString(obj: Record<string, unknown> | null, key: string): string | null {
	const v = obj?.[key];
	return typeof v === 'string' && v !== '' ? v : null;
}

/**
 * D-03 (47-DVF-MINIMIZATION-REVIEW.md §5) accommodation -- the MASKING DECISION lives entirely
 * in the route's load() (+page.ts, applied before serialization); this mapper never decides
 * whether/how to mask. But the registry-driven `formatField`/'currency' formatter expects a
 * NUMERIC value (fiche-fields.v1.json) and would degrade any non-numeric string to a hard gap
 * ("non disponible") -- which would defeat the bucket's purpose (a readable price RANGE, not an
 * absence). This reader renders an already-masked bucket string (e.g. "200 000 – 250 000 €")
 * verbatim, and falls back to the normal numeric formatting path otherwise -- purely mechanical,
 * no isolation/window logic here.
 */
function dvfMoneyRow(fieldId: string, label: string, row: Record<string, unknown> | null, key: string): KvRow {
	const raw = row?.[key];
	if (typeof raw === 'string' && !Number.isFinite(Number(raw))) {
		// Phase 49 (D-18): this is one of the two KvRow returned OUTSIDE formatField, so it must
		// attach its own ℹ️ key and state — a masked price BRACKET is a genuinely displayed value
		// ('value'), not a gap. The masking DECISION still lives entirely in the route's load()
		// (D-03 Phase 47); nothing about it is decided or re-decided here.
		return { label, value: raw, fieldId, state: 'value' };
	}
	return formatNullableField(fieldId, label, readNumber(row, key));
}

// --- L0 synthese (bien-level KPI) ---

function buildBienKpi(
	multi: ResultatMultiResolution,
	selectionDpe: DpeFicheSelection,
	dvf: DvfAttributionView
): KpiItem[] {
	// D-10 : la lettre ne peut venir que du diagnostic AUTORISÉ — état `exact`, ou candidat de
	// `multiple` dont l'appartenance a été vérifiée. Dans les quatre autres cas elle est absente,
	// et le KPI dit `n.d.` plutôt que d'emprunter celle d'un voisin ou du bâtiment.
	const etiquette = selectionDpe.diagnostic?.etiquette_dpe ?? null;

	// D-16/D-37 : le prix ne vient QUE de la mutation que la décision autorise à montrer. Aucune
	// lecture du bloc DVF ici — c'est exactement le raccourci que cette phase supprime.
	const prixM2Brut = dvf.mutation?.prix_m2 ?? null;
	// D-03 masked bucket (see dvfMoneyRow docstring) -- render verbatim in the KPI too.
	const prixM2Masque =
		typeof prixM2Brut === 'string' && !Number.isFinite(Number(prixM2Brut)) ? prixM2Brut : null;
	const prixM2 = prixM2Masque == null ? asNumber(prixM2Brut) : null;

	const cadastreState = datasetState(multi, 'cadastre');
	const cadastre = cadastreState === 'ok' ? asObject(multi.datasets?.cadastre) : null;
	const parcelle = readString(cadastre, 'id_parcelle');

	// 1. Étiquette DPE — positioned on the OFFICIAL A→G scale (published, openable), not on a
	// local copy of its bands. null / unknown class → info without a label (never invented).
	const dpe = comparerAuReferentiel('bien.dpe.etiquette_dpe', etiquette?.toUpperCase() ?? null);
	const dpeKpi: KpiItem = {
		label: 'Étiquette DPE',
		value: etiquette ?? NA,
		verdict: dpe?.verdict ?? 'info',
		verdictLabel: dpe?.label,
		ecartTexte: dpe?.ecartTexte,
		fieldId: 'bien.dpe.etiquette_dpe',
		presentation: {
			state: etiquette != null ? 'value' : selectionDpe.diagnostic != null ? 'not_reported' : dpeGapState(selectionDpe).state,
			grain: 'Logement'
		},
		primary: true,
		dpeClass: etiquette ? etiquette.toUpperCase() : undefined
	};

	// 2. Prix au m² d'une transaction de la parcelle — D-07 registered in SANS_REFERENTIEL: a PAST
	// SALE price is not a valuation, so it carries NO POSITIONING at all. The chip below is not one
	// either: `verdict` stays `info` and `verdictLabel` carries the ÉTAT — « Non attribuée »
	// (D-46), or the exact reason there is no attributable sale (D-16). It is the
	// only always-visible one-line slot of a KPI, and D-21 forbids restructuring the fiche to add
	// another. The fieldId stays even when the price is shown as a masked bracket: the ℹ️ explains
	// the METRIC, not the particular value rendered.
	const prixKpi: KpiItem = {
		label: dvf.kpiLabel,
		value: prixM2Masque ?? (prixM2 == null ? NA : formatBareNumber('bien.dvf.prix_m2', prixM2)),
		unit: prixM2Masque ? undefined : '€/m²',
		verdict: 'info',
		verdictLabel: dvf.mention ?? undefined,
		ecartTexte: dvf.mentionDetaillee,
		fieldId: 'bien.dvf.prix_m2',
		presentation: {
			state: prixM2Masque != null || prixM2 != null ? 'value' : dvf.mutation != null ? 'not_reported' : dvf.gapState ?? 'out_of_coverage',
			grain: 'Parcelle',
			reserves: [dvf.mention, dvf.mentionDetaillee].filter((text): text is string => text != null)
		},
		primary: true
	};

	// 3. Parcelle cadastrale — a pure identifier: no unit, nothing to interpret, so it is excluded
	// from the ℹ️ coverage by D-15 and carries NO fieldId.
	const parcelleKpi: KpiItem = {
		label: 'Parcelle cadastrale',
		value: parcelle ?? NA,
		presentation: {
			state: parcelle != null ? 'value' : cadastreState === 'ok' ? 'not_reported' : datasetGapState(cadastreState).state,
			grain: 'Parcelle'
		},
		verdict: 'info'
	};

	return [dpeKpi, prixKpi, parcelleKpi];
}

// --- per-dataset bien-level sub-mappers (each reads its state, degrades if not 'ok') ---

function mapDpeRows(dpe: DpeFicheSelection): KvRow[] {
	const diagnostic = dpe.diagnostic;
	if (diagnostic == null) {
		// Aucune première ligne arbitraire à rendre : sans diagnostic autorisé, le thème Énergie
		// se dégrade entièrement, en portant la raison exacte de l'écart.
		const gap = dpeGapState(dpe);
		return [
			formatField('bien.dpe.etiquette_dpe', 'Étiquette DPE', gap),
			formatField('bien.dpe.etiquette_ges', 'Étiquette GES', gap),
			formatField('bien.dpe.surface_habitable', 'Surface habitable', gap)
		];
	}
	const coutEnergie = diagnostic.cout_total_5_usages;
	const energieChauffage = diagnostic.type_energie_principale_chauffage;
	const isolation = diagnostic.qualite_isolation_enveloppe;
	return [
		formatNullableField('bien.dpe.etiquette_dpe', 'Étiquette DPE', diagnostic.etiquette_dpe),
		formatNullableField('bien.dpe.etiquette_ges', 'Étiquette GES', diagnostic.etiquette_ges),
		formatNullableField(
			'bien.dpe.surface_habitable',
			'Surface habitable',
			diagnostic.surface_habitable
		),
		kvConstruction(diagnostic.annee_construction, diagnostic.periode_construction),
		formatNullableField('bien.dpe.type_batiment', 'Type de bâtiment', diagnostic.type_batiment),
		// D-03: the diagnostic exists — a null ADEME field is "Non renseigné", not the generic
		// record-absent gap (formatNullableField's default out_of_coverage fallback). A candidate
		// chosen inside `multiple` legitimately carries fewer fields than an `exact` diagnostic:
		// the backend minimised its payload, and inventing the rest is precisely what is banned.
		coutEnergie == null
			? kvSourceNull('bien.dpe.cout_total_5_usages', 'Coût énergie estimé')
			: formatField('bien.dpe.cout_total_5_usages', 'Coût énergie estimé', {
					state: 'value',
					value: coutEnergie
				}),
		energieChauffage == null
			? kvSourceNull('bien.dpe.energie_chauffage', 'Énergie de chauffage')
			: formatField('bien.dpe.energie_chauffage', 'Énergie de chauffage', {
					state: 'value',
					value: energieChauffage
				}),
		isolation == null
			? kvSourceNull('bien.dpe.isolation', 'Isolation (enveloppe)')
			: formatField('bien.dpe.isolation', 'Isolation (enveloppe)', {
					state: 'value',
					value: isolation
				}),
		formatNullableField(
			'bien.dpe.conso_5_usages_ep',
			'Consommation annuelle (5 usages EP)',
			diagnostic.conso_5_usages_ep
		)
	];
}

/**
 * Une transaction ANTÉRIEURE de la parcelle, réduite à ce qui la rend lisible : sa date et
 * l'étiquette de ce qu'elle est (D-11/D-17). Une vente d'ensemble, une vente mixte ou une vente
 * sur plan reste donc VISIBLE dans l'historique, nommée pour ce qu'elle est — elle ne disparaît
 * pas, et elle ne se déguise pas en vente de ce logement. Son montant n'est pas rendu : un prix
 * global n'a pas de lecture au niveau du bien, et les tranches de la minimisation D-03 ne
 * couvrent que la transaction la plus récente.
 */
function ligneHistoriqueDvf(row: Record<string, unknown>, index: number): KvRow {
	// La date passe par l'autorité de formatage unique (registre + invariant DATA-05) : une date
	// illisible ou future dégrade la ligne au lieu d'être rendue telle quelle.
	const date = formatNullableField(
		'bien.dvf.date_mutation',
		COPIE_DVF.lignesBien.historique,
		readString(row, 'date_mutation')
	).value;
	const etiquette = etiquetteMutation(
		readString(row, 'classe_mutation'),
		row['hors_stats_vefa'] === true
	);
	const formatted = formatField('bien.dvf.classe_mutation', COPIE_DVF.lignesBien.historique, {
		state: 'value',
		// D-46 : séparateur point médian, comme « Parcelle · Nantes » ailleurs sur la Fiche. Aucun
		// tiret cadratin ne subsiste dans une chaîne DVF affichée.
		value: `${date} · ${etiquette}`
	});
	// The public DVF contract exposes id_mutation; never use a DPE document reference here.
	// Old payloads without a source identity retain a local position, not an invented identity.
	return { ...formatted, id: `dvf-history:${readString(row, 'id_mutation') ?? `legacy-position:${index}`}` };
}

/** Mutation counters describe a perimeter; they never reconstruct individual locals. */
function contexteMutationDvf(row: Record<string, unknown>, index: number): KvRow {
	const counters = [
		['nb_locaux_maison', 'Maisons'],
		['nb_locaux_appartement', 'Appartements'],
		['nb_locaux_commercial', 'Locaux commerciaux'],
		['nb_parcelles', 'Parcelles']
	] as const;
	const perimeter = counters.flatMap(([field, label]) => {
		const count = readNumber(row, field);
		return count !== null && Number.isSafeInteger(count) && count >= 0
			? [`${label} : ${count}`]
			: [];
	});
	const date = formatNullableField('bien.dvf.date_mutation', 'Date de mutation', readString(row, 'date_mutation')).value;
	return {
		id: `dvf-perimeter:${readString(row, 'id_mutation') ?? `legacy-position:${index}`}`,
		kind: 'context',
		state: 'value',
		label: `Périmètre de la mutation · ${date}`,
		value: [
			...(perimeter.length ? [`Totaux de mutation · ${perimeter.join(' · ')}.`] : []),
			'Détail par local indisponible : les données décrivent l’ensemble de la vente et ne permettent pas d’identifier chaque local.'
		].join(' '),
		presentation: { state: 'value', grain: 'Mutation observée sur la parcelle' }
	};
}

function mapDvfRows(multi: ResultatMultiResolution): KvRow[] {
	const copie = COPIE_DVF.lignesBien;
	const state = datasetState(multi, 'dvf');
	if (state !== 'ok') {
		const gap = datasetGapState(state);
		return [
			formatField('bien.dvf.prix_m2', copie.gapPrix, gap),
			formatField('bien.dvf.date_mutation', copie.gapDate, gap)
		];
	}
	// L'historique de la PARCELLE — il reste lu ici, et c'est légitime (D-11/D-17) : cette liste
	// n'attribue rien, elle est qualifiée par la décision, qui vit à part.
	const mutations = asList(multi.datasets?.dvf);
	const row = mutations[0] ?? null;
	return [
		formatNullableField(
			'bien.dvf.classe_mutation',
			copie.classe,
			row == null
				? null
				: etiquetteMutation(readString(row, 'classe_mutation'), row['hors_stats_vefa'] === true)
		),
		dvfMoneyRow('bien.dvf.valeur_fonciere', copie.valeurFonciere, row, 'valeur_fonciere'),
		dvfMoneyRow('bien.dvf.prix_m2', copie.prixM2, row, 'prix_m2'),
		formatNullableField('bien.dvf.date_mutation', copie.date, readString(row, 'date_mutation')),
		formatNullableField('bien.dvf.type_local', copie.typeLocal, readString(row, 'type_local')),
		formatNullableField(
			'bien.dvf.surface_bati_totale',
			'Surface bâtie totale de la mutation',
			readNumber(row, 'surface_bati_totale')
		),
		formatNullableField(
			'bien.dvf.surface_reelle_bati',
			'Surface réelle bâtie totale de la mutation',
			readNumber(row, 'surface_reelle_bati')
		),
		formatNullableField(
			'bien.dvf.nombre_pieces_principales',
			'Nombre total de pièces de la mutation',
			readNumber(row, 'nombre_pieces_principales')
		),
		...(row ? [contexteMutationDvf(row, 0)] : []),
		...mutations.slice(1).flatMap((mutation, index) => [
			ligneHistoriqueDvf(mutation, index),
			contexteMutationDvf(mutation, index + 1)
		])
	];
}

function mapCadastreRows(multi: ResultatMultiResolution): KvRow[] {
	const state = datasetState(multi, 'cadastre');
	if (state !== 'ok') {
		const gap = datasetGapState(state);
		return [
			formatField('bien.cadastre.id_parcelle', 'Parcelle cadastrale', gap),
			formatField('bien.cadastre.contenance', 'Contenance', gap)
		];
	}
	const obj = asObject(multi.datasets?.cadastre);
	return [
		formatNullableField('bien.cadastre.id_parcelle', 'Parcelle cadastrale', readString(obj, 'id_parcelle')),
		formatNullableField('bien.cadastre.section', 'Section', readString(obj, 'section')),
		formatNullableField('bien.cadastre.numero', 'Numéro', readString(obj, 'numero')),
		formatNullableField('bien.cadastre.contenance', 'Contenance', readNumber(obj, 'contenance'))
	];
}

function mapRisquesRows(
	multi: ResultatMultiResolution
): { rowsBien: KvRow[]; rowsCommune: KvRow[] } {
	const state = datasetState(multi, 'risques');
	if (state !== 'ok') {
		const gap = datasetGapState(state);
		return {
			rowsBien: [
				formatField('bien.risques.expo_argiles', 'Retrait-gonflement argiles (au point)', gap)
			],
			rowsCommune: [
				formatField('bien.risques.inondation', 'Inondation recensée (commune)', gap),
				formatField('bien.risques.sismique', 'Séisme recensé (commune)', gap)
			]
		};
	}
	// Georisques aggregates COMMUNE-level natural risks as risques_naturels[].risques_detail[],
	// each detail carrying a `libelle_risque_long` (WR-05: key names confirmed against a live
	// /v1/resolve response on 2026-06-04 — the previous sismicite/radon/inondation guesses were wrong).
	const obj = asObject(multi.datasets?.risques);
	const libelles = new Set<string>();
	for (const entry of asList(obj?.['risques_naturels'])) {
		for (const detail of asList(asObject(entry)?.['risques_detail'])) {
			const lib = readString(asObject(detail), 'libelle_risque_long');
			if (lib) libelles.add(lib.toLowerCase());
		}
	}
	const sources = asObject(obj?.['sources']);
	const naturalSource = asObject(sources?.['risques_naturels']);
	const flagRow = (fieldId: string, label: string, keywords: string[]): KvRow => {
		if (naturalSource?.['statut'] === 'indisponible') {
			return formatField(fieldId, label, { state: 'temporary_error' });
		}
		const present = keywords.some((k) => [...libelles].some((l) => l.includes(k)));
		if (present) return formatField(fieldId, label, { state: 'value', value: true });
		// A first page (or legacy response without coverage) cannot prove absence.
		if (naturalSource?.['complet'] !== true) {
			return formatField(fieldId, label, { state: 'not_reported' });
		}
		return formatField(fieldId, label, { state: 'value', value: false });
	};
	// RGA is queried by coordinates. Keep its point exposure separate from GASPAR's commune list.
	const expoArgiles = readString(asObject(obj?.['argiles']), 'exposition');
	return {
		rowsBien: [
			asObject(sources?.['argiles'])?.['statut'] === 'indisponible'
				? formatField('bien.risques.expo_argiles', 'Retrait-gonflement argiles (au point)', { state: 'temporary_error' })
				: formatNullableField('bien.risques.expo_argiles', 'Retrait-gonflement argiles (au point)', expoArgiles)
		],
		rowsCommune: [
			flagRow('bien.risques.inondation', 'Inondation recensée (commune)', ['inondation']),
			flagRow('bien.risques.sismique', 'Séisme recensé (commune)', ['séisme', 'seisme']),
			...(naturalSource?.['complet'] !== true ? [{
				kind: 'context' as const, state: 'not_reported' as const, label: 'Couverture GASPAR',
				value: naturalSource?.['statut'] === 'indisponible'
					? 'Sous-source indisponible — risques non vérifiés'
					: `${naturalSource?.['nombre_retournes'] ?? 'Nombre inconnu de'} résultats lus ; total ${naturalSource?.['total'] ?? 'inconnu'}. Première page, exhaustivité non établie.`,
				muted: true
			}] : [])
		]
	};
}

function mapUrbanismeRows(multi: ResultatMultiResolution): KvRow[] {
	const state = datasetState(multi, 'urbanisme');
	if (state !== 'ok') {
		return [formatField('bien.urbanisme.zonage_libelle', 'Zonage PLU (au point)', datasetGapState(state))];
	}
	const obj = asObject(multi.datasets?.urbanisme);
	const sources = asObject(obj?.['sources']);
	const zonesSource = asObject(sources?.['zones_plu']);
	const prescriptionSource = asObject(sources?.['prescriptions']);
	const zones = asList(obj?.['zones_plu']);
	const properties = zones.map(zone => asObject(zone?.['properties']) ?? asObject(zone));
	const labels = properties.map(props => readString(props, 'libelle')).filter((value): value is string => value !== null).sort();
	const types = properties.map(props => readString(props, 'typezone')).filter((value): value is string => value !== null).sort();
	const rows: KvRow[] = zonesSource?.['statut'] === 'indisponible'
		? [formatField('bien.urbanisme.zonage_libelle', 'Zonage PLU (au point)', { state: 'temporary_error' })]
		: [
			formatNullableField('bien.urbanisme.zonage_libelle', zones.length > 1 ? `Zonages PLU candidats (${zones.length})` : 'Zonage PLU (au point)', labels.join(' ; ') || null),
			formatNullableField('bien.urbanisme.type_zone', zones.length > 1 ? 'Types des zones candidates' : 'Type de zone (au point)', types.join(' ; ') || null)
		];
	if (prescriptionSource?.['statut'] === 'indisponible') {
		rows.push({ kind: 'context', label: 'Prescriptions GPU', value: 'Sous-source indisponible — absence de prescription non vérifiée', muted: true, state: 'value' });
	} else if (prescriptionSource?.['complet'] !== true || zonesSource?.['complet'] !== true) {
		rows.push({ kind: 'context', label: 'Lecture GPU', value: 'Première page ; exhaustivité des zones ou prescriptions non établie', muted: true, state: 'not_reported' });
	}
	return rows;
}

// --- commune-side rows: reuse mapCommuneToFiche, keyed by theme ---

function communeRowsByKey(commune: CommuneSynthese | null): Record<string, KvRow[]> {
	if (commune == null) return {};
	const { themes } = mapCommuneToFiche(commune);
	const byKey: Record<string, KvRow[]> = {};
	for (const t of themes) byKey[t.key] = t.rows;
	return byKey;
}

// --- L1/L2 themes (5, always present), each carrying rowsBien + rowsCommune ---

function buildBienThemes(
	multi: ResultatMultiResolution,
	commune: CommuneSynthese | null,
	dpe: DpeFicheSelection
): ThemeModel[] {
	const communeRows = communeRowsByKey(commune);
	const risquesRows = mapRisquesRows(multi);

	const marche: ThemeModel = {
		key: 'marche',
		emoji: '💶',
		name: 'Marché & prix',
		hint: 'Ventes récentes (DVF)',
		rows: [],
		rowsBien: mapDvfRows(multi),
		rowsCommune: communeRows['marche'] ?? [],
		sources: 'DVF, Loyers de référence, Zonage ABC'
	};

	const energie: ThemeModel = {
		key: 'energie',
		emoji: '⚡',
		name: 'Énergie & bâti',
		hint: 'DPE du bien',
		rows: [],
		rowsBien: mapDpeRows(dpe),
		rowsCommune: communeRows['energie'] ?? [],
		sources: 'DPE (ADEME), Démographie (logements)'
	};

	const risques: ThemeModel = {
		key: 'risques',
		emoji: '⚠️',
		name: 'Risques',
		hint: 'Argiles au point, risques communaux',
		rows: [],
		rowsBien: risquesRows.rowsBien,
		rowsCommune: [
			// Replace the synthesis-only placeholder with the available live commune observations.
			...(communeRows['risques'] ?? []).filter(
				(row) => row.fieldId !== 'commune.risques.risques_naturels'
			),
			...risquesRows.rowsCommune
		],
		sources: 'Géorisques, Zonage ABC'
	};

	const environnement: ThemeModel = {
		key: 'environnement',
		emoji: '🏘️',
		name: 'Environnement',
		hint: 'Urbanisme du bien, démographie',
		rows: [],
		rowsBien: mapUrbanismeRows(multi),
		rowsCommune: communeRows['environnement'] ?? [],
		sources: 'Urbanisme (GPU), Démographie, Revenus'
	};

	const propriete: ThemeModel = {
		key: 'propriete',
		emoji: '📋',
		name: 'Propriété',
		hint: 'Parcelle cadastrale',
		rows: [],
		rowsBien: mapCadastreRows(multi),
		rowsCommune: communeRows['propriete'] ?? [],
		sources: 'Cadastre, Vacance, Fiscalité locale'
	};

	return [marche, energie, risques, environnement, propriete];
}

/**
 * Map a multi-dataset resolution + its commune synthese to the Bien Fiche model:
 * bien-level L0 KPI + 5 L1/L2 themes, each split into rowsBien (« sur ce bien ») and
 * rowsCommune (« dans la commune », reused from mapCommuneToFiche). Degrades per-dataset
 * (3 states), classes ADRESSE_INCOMPLETE warnings commune-side (Pitfall 3, enforced inside
 * datasetState), invents nothing.
 *
 * `dpe` is the ONLY door through which a diagnostic reaches the KPI and the energy theme
 * (D-10/D-18). It is decided upstream by `deriveDpeAttributionView`, from the backend's closed
 * decision — never here, and never from the payload. Its default is deliberately fail-closed:
 * an incomplete call site shows `n.d.`, it does not fall back on a list.
 *
 * The ADRESSE_INCOMPLETE guard no longer covers the DPE, and does not need to: without a street
 * number there is no BAN key, so the policy answers `absent/identity_unavailable` and the
 * selection is empty by construction — a structural guarantee instead of a warning this mapper
 * could forget to read.
 */
export function mapBienToFiche(
	multi: ResultatMultiResolution,
	commune: CommuneSynthese | null,
	dpe: DpeFicheSelection = SELECTION_DPE_INDISPONIBLE
): { kpi: KpiItem[]; themes: ThemeModel[] } {
	// La décision DVF voyage DANS l'enveloppe resolve (contrairement au DPE, qui exige un second
	// appel) : elle est donc dérivée ici, une seule fois, et aucun appelant n'a à la fournir. Une
	// enveloppe sans décision donne une vue fermée — indisponibilité, jamais « aucune vente ».
	const dvf = deriveDvfAttributionView(multi.dvf_attribution);
	return {
		kpi: buildBienKpi(multi, dpe, dvf),
		themes: buildBienThemes(multi, commune, dpe)
	};
}
