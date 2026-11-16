import { readFileSync } from 'node:fs';
import { resolve as joinPath } from 'node:path';
import { describe, it, expect } from 'vitest';
import { mapBienToGrid } from './bien-grid';
import { bienThemeCsvRows } from './bien-theme-csv';
import { resolutionPourEtat } from './__fixtures__/dvf-qualification';
import { mapBienToFiche, SELECTION_DPE_INDISPONIBLE } from './bien-fiche';
import { deriveDpeAttributionView, type DpeFicheSelection } from './dpe-attribution-view';
import type {
	DpeAttribution,
	DpePublicDiagnostic,
	DvfAttribution,
	DvfMutation,
	ResultatMultiResolution
} from '$lib/api/resolve';
import { COPIE_DVF } from './dvf-attribution-view';
import { assertFieldsCovered, SemanticContractError } from './formatters';
import { formatPriceBucket } from './dvf-minimisation';
import {
	mockResolve85,
	mockResolveAllEmpty,
	mockResolveErrorRisques,
	mockResolveWithAvertissement
} from './__fixtures__/resolve-85';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';

/** Find a theme by key, asserting it exists. */
function theme(themes: ReturnType<typeof mapBienToFiche>['themes'], key: string) {
	const t = themes.find((x) => x.key === key);
	expect(t, `theme ${key} should exist`).toBeTruthy();
	return t!;
}

describe('59.2 — périmètre des mutations sans locaux reconstruits', () => {
	const reserve = 'Détail par local indisponible';
	const normalize = (value: string) => value.replace(/[\s\u202f\u00a0]/g, '');
	function outputs(rows: Record<string, unknown>[]) {
		const base = resolutionPourEtat('ensemble');
		const model = mapBienToGrid({ ...base, datasets: { ...base.datasets, dvf: rows } }, null);
		const market = theme(model.fiche.themes, 'marche');
		return {
			rows: market.rowsBien!,
			grid: model.themes.marche.bien,
			csv: bienThemeCsvRows(market),
			text: market.rowsBien!.map(row => `${row.label} ${row.value}`).join('\n')
		};
	}

	it.each(['ensemble', 'mixte', 'compatible_parcelle', 'vefa_sans_surface', 'vente_sur_plan'] as const)(
		'%s conserve le grain mutation et la réserve dans la grille et le CSV', category => {
			const base = resolutionPourEtat(category);
			const result = outputs(base.datasets!.dvf as Record<string, unknown>[]);
			const contexts = result.rows.filter(row => row.kind === 'context' && row.value.includes(reserve));
			expect(contexts).toHaveLength((base.datasets!.dvf as unknown[]).length);
			expect(contexts.every(row => row.presentation?.grain === 'Mutation observée sur la parcelle')).toBe(true);
			const gridContexts = result.grid.rows.flatMap(row => Object.values(row.cells))
				.filter(cell => cell.kind === 'context' && cell.text.includes(reserve));
			expect(gridContexts).toHaveLength(contexts.length);
			expect(result.csv.filter(row => row[1].includes(reserve))).toHaveLength(contexts.length);
			expect(result.text).not.toMatch(/Local [1-9]|Logement [1-9]/);
		}
	);

	it('garde les compteurs distincts, zéro explicite et surfaces totales sans quotient', () => {
		const base = (resolutionPourEtat('ensemble').datasets!.dvf as Record<string, unknown>[])[0];
		const result = outputs([{ ...base, nb_locaux_maison: 0, nb_locaux_appartement: 4,
			nb_locaux_commercial: 0, nb_parcelles: 2, nb_lots_source: 99 }]);
		expect(result.text).toContain('Maisons : 0');
		expect(result.text).toContain('Appartements : 4');
		expect(result.text).toContain('Locaux commerciaux : 0');
		expect(result.text).toContain('Parcelles : 2');
		expect(result.text).not.toContain('99');
		expect(result.text).toContain('totale de la mutation');
		expect(normalize(result.text).match(/640000€/g)).toHaveLength(1);
		expect(normalize(result.text)).not.toContain('2519');
		const absent = outputs([base]);
		expect(absent.text).not.toMatch(/Maisons :|Appartements :|Locaux commerciaux :|Parcelles :/);
		expect(absent.text).toContain(reserve);
	});

	it('trois mutations ont des concepts distincts sans fuite des montants historiques, y compris le CSV', () => {
		const base = (resolutionPourEtat('ensemble').datasets!.dvf as Record<string, unknown>[])[0];
		const rows: Record<string, unknown>[] = [
			{ ...base, valeur_fonciere: '600 000 – 650 000 €' },
			{ ...base, id_mutation: 'SYNTH-HISTORY-A', date_mutation: '2019-04-02', valeur_fonciere: 981234, prix_m2: 8765 },
			{ ...base, id_mutation: 'SYNTH-HISTORY-B', date_mutation: '2018-03-01', valeur_fonciere: 765432, prix_m2: 6543 }
		];
		const result = outputs(rows);
		const ids = result.grid.rows.map(row => row.concept.id);
		expect(new Set(ids).size).toBe(ids.length);
		const contexts = result.rows.filter(row => row.kind === 'context' && row.value.includes(reserve));
		expect(contexts).toHaveLength(3);
		expect(contexts.map(row => row.id)).toEqual(outputs(rows).rows.filter(row => row.kind === 'context' && row.value.includes(reserve)).map(row => row.id));
		for (const text of [result.text, JSON.stringify(result.csv), JSON.stringify(result.grid)]) {
			const normalized = normalize(text);
			for (const hidden of ['981234', '8765', '765432', '6543']) expect(normalized).not.toContain(hidden);
		}
		expect(result.text.match(/600 000 – 650 000 €/g)).toHaveLength(1);
		expect(result.text).toContain('2019');
		expect(result.text).toContain('2018');
		const legacy = outputs(rows.map(({ id_mutation: _id, ...row }) => row));
		expect(new Set(legacy.grid.rows.map(row => row.concept.id)).size).toBe(legacy.grid.rows.length);
		expect(legacy.rows.filter(row => row.kind === 'context' && row.value.includes(reserve))).toHaveLength(3);
	});
});

// --- Phase 57 : le prix vient de la DÉCISION DVF, plus du bloc de données (D-16/D-37) --------

/** Le KPI de prix, repéré par sa clé ℹ️ — jamais par un libellé, qui est de la copie provisoire. */
function kpiDvf(fiche: ReturnType<typeof mapBienToFiche>) {
	const item = fiche.kpi.find((k) => k.fieldId === 'bien.dvf.prix_m2');
	expect(item, 'le KPI de prix DVF doit exister').toBeTruthy();
	return item!;
}

function mutation(partiel: Partial<DvfMutation> = {}): DvfMutation {
	return {
		id_mutation: '2023-123456',
		date_mutation: '2023-09-12',
		nature_mutation: 'Vente',
		valeur_fonciere: 168000,
		type_local: 'Appartement',
		surface_reelle_bati: 78,
		nombre_pieces_principales: 3,
		prix_m2: 2154,
		id_parcelle: '85047000AB0042',
		classe_mutation: 'residentiel_appartement',
		prix_m2_raison: null,
		mutation_mono_local: true,
		surface_bati_totale: 78,
		hors_stats_vefa: false,
		...partiel
	};
}

/** L'état positif le plus fort que la Phase 57 puisse produire (D-37 : jamais `exacte`). */
function compatible(partiel: Partial<DvfMutation> = {}): DvfAttribution {
	return {
		status: 'compatible_parcelle',
		mutation: mutation(partiel),
		rule_version: '57-17/D-47',
		evidence: ['type_compatible', 'mono_local', 'nature_vente', 'surface_presente'],
		perimetre_ensemble: false,
		nb_locaux_residentiel: 1,
		nb_locaux_commercial: 0,
		nb_lots_source: 1
	};
}

function nonAttribuable(
	reason: Extract<DvfAttribution, { status: 'non_attribuable' }>['reason'],
	ensemble = false
): DvfAttribution {
	return {
		status: 'non_attribuable',
		reason,
		perimetre_ensemble: ensemble,
		nb_locaux_residentiel: ensemble ? 4 : 1,
		nb_locaux_commercial: 0
	};
}

/** Une enveloppe resolve portant LA décision DVF sous test, sans rien changer d'autre. */
function avecDecisionDvf(attribution: DvfAttribution | null): ResultatMultiResolution {
	return { ...mockResolve85, dvf_attribution: attribution };
}

// --- Phase 51 : le DPE de la fiche vient d'une SÉLECTION, plus d'un bloc de données ----------
//
// `mapBienToFiche` ne cherche plus « le » DPE dans la charge utile : il reçoit la sélection
// autorisée par la décision backend. Les fixtures ci-dessous construisent donc un diagnostic
// typé, et la matrice plus bas fait passer les cinq états par le vrai mapper d'attribution —
// aucun raccourci qui court-circuiterait la règle sous test.

function diagnostic(partiel: Partial<DpePublicDiagnostic> = {}): DpePublicDiagnostic {
	return {
		numero_dpe: '2385E1234567A',
		etage: null,
		batiment: null,
		porte: null,
		type_batiment: 'maison',
		surface_habitable: 78,
		etiquette_dpe: 'F',
		etiquette_ges: 'D',
		annee_construction: 1972,
		periode_construction: '1948-1974',
		type_installation_chauffage: 'chaudière fioul',
		type_energie_principale_chauffage: 'Fioul domestique',
		conso_5_usages_ep: 412,
		emission_ges_5_usages: 98,
		cout_total_5_usages: 2310,
		qualite_isolation_enveloppe: 'insuffisante',
		date_etablissement_dpe: '2022-06-01',
		date_fin_validite_dpe: '2032-05-31',
		annee_diagnostic: 2022,
		perime: false,
		...partiel
	};
}

/** The authorised selection of an `exact` attribution — the nominal Fiche case. */
function selectionExacte(partiel: Partial<DpePublicDiagnostic> = {}): DpeFicheSelection {
	return { diagnostic: diagnostic(partiel), gapState: null };
}

/** Run a whole backend decision through the real attribution mapper, then through the Fiche. */
function ficheDepuisAttribution(attribution: DpeAttribution, ancre?: string) {
	const vue = deriveDpeAttributionView(attribution, { hrefBase: '/bien/x', dpe: ancre });
	return mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, vue.ficheSelection);
}

describe('audit septembre 2026 — unités et portée des données', () => {
	it.each([80, null])('conserve le total annuel DPE avec une surface de %s m²', (surface) => {
		const selection = selectionExacte({ surface_habitable: surface, conso_5_usages_ep: 12000 });
		const fiche = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, selection);
		const consommation = theme(fiche.themes, 'energie').rowsBien?.find(
			(row) => row.fieldId === 'bien.dpe.conso_5_usages_ep'
		);
		expect(consommation?.value).toBe('12\u202f000 kWhEP/an');
		expect(selection.diagnostic?.conso_5_usages_ep).toBe(12000);
		expect(consommation?.value).not.toContain('/m²');
	});

	it('sépare les risques communaux de l’exposition aux argiles au point consulté', () => {
		const fiche = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const risques = theme(fiche.themes, 'risques');
		const communaux = ['bien.risques.inondation', 'bien.risques.sismique'];
		for (const fieldId of communaux) {
			expect(risques.rowsBien?.some((row) => row.fieldId === fieldId)).toBe(false);
			const row = risques.rowsCommune?.find((item) => item.fieldId === fieldId);
			expect(row?.value).toBe('Oui');
			// CSV flattens both tables: the label must retain its scope outside the page.
			expect(row?.label).toContain('(commune)');
		}
		expect(risques.rowsBien?.find((row) => row.fieldId === 'bien.risques.expo_argiles'))
			.toMatchObject({ value: 'Exposition moyenne' });
		expect(risques.rowsCommune?.some((row) => row.fieldId === 'commune.risques.risques_naturels'))
			.toBe(false);
		expect(risques.hint).not.toContain('par parcelle');
		expect(fiche.kpi.some((item) => item.fieldId?.startsWith('bien.risques.'))).toBe(false);
		expect(theme(fiche.themes, 'environnement').rowsBien?.find(
			(row) => row.fieldId === 'bien.urbanisme.zonage_libelle'
		)).toMatchObject({ value: 'Ua' });
	});
});

describe('mapBienToFiche() — datasetState 4 branches', () => {
	it("'ok': a selected diagnostic renders real values (dpe etiquette F visible)", () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, selectionExacte());
		const energie = theme(themes, 'energie');
		const dpeRow = (energie.rowsBien ?? []).find((r) => /étiquette dpe/i.test(r.label));
		expect(dpeRow).toBeTruthy();
		expect(dpeRow!.available).not.toBe(false);
		expect(dpeRow!.value).toContain('F');
	});

	it("'empty': no authorised diagnostic → available:false on the energy bien rows", () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, {
			diagnostic: null,
			gapState: 'out_of_coverage'
		});
		const energie = theme(themes, 'energie');
		const dpeRow = (energie.rowsBien ?? []).find((r) => /étiquette dpe/i.test(r.label));
		expect(dpeRow).toBeTruthy();
		expect(dpeRow!.available).toBe(false);
	});

	it("'ok': diagnostic present but ADEME fields null → 'Non renseigné' + muted:true (D-03, distinct from record-absent)", () => {
		const { themes } = mapBienToFiche(
			mockResolve85,
			COMMUNE_85047_CHALLANS,
			selectionExacte({
				annee_construction: null,
				periode_construction: null,
				cout_total_5_usages: null,
				type_energie_principale_chauffage: null,
				qualite_isolation_enveloppe: null
			})
		);
		const rows = theme(themes, 'energie').rowsBien ?? [];
		const construction = rows.find((r) => /année de construction/i.test(r.label));
		const cout = rows.find((r) => /coût énergie estimé/i.test(r.label));
		const chauffage = rows.find((r) => /énergie de chauffage/i.test(r.label));
		const isolation = rows.find((r) => /isolation/i.test(r.label));

		for (const row of [construction, cout, chauffage, isolation]) {
			expect(row).toBeTruthy();
			expect(row!.value).toBe('Non renseigné');
			expect(row!.available).not.toBe(false);
			expect(row!.muted).toBe(true);
		}
	});

	it("'empty': no diagnostic at all → gap rows stay available:false, unmuted (record-absent distinct from field-null, D-03)", () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, {
			diagnostic: null,
			gapState: 'out_of_coverage'
		});
		const rows = theme(themes, 'energie').rowsBien ?? [];
		expect(rows.length).toBeGreaterThan(0);
		expect(rows.every((r) => r.available === false && r.muted !== true)).toBe(true);
	});

	it("'error': a dataset in erreurs[] degrades ITS theme only, other themes still render", () => {
		const { themes } = mapBienToFiche(mockResolveErrorRisques, COMMUNE_85047_CHALLANS);
		// Risques theme is degraded (risques in erreurs[]).
		const risques = theme(themes, 'risques');
		expect((risques.rowsBien ?? []).every((r) => r.available === false)).toBe(true);
		for (const fieldId of ['bien.risques.inondation', 'bien.risques.sismique']) {
			expect(risques.rowsCommune?.find((row) => row.fieldId === fieldId))
				.toMatchObject({ available: false, state: 'temporary_error' });
		}
		// Marché theme still renders real DVF data (isolation by theme — D-04).
		const marche = theme(themes, 'marche');
		expect((marche.rowsBien ?? []).some((r) => r.available !== false)).toBe(true);
	});

	it("'ok': risques reads the real Georisques shape, not the old guessed keys (WR-05 regression)", () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const risques = theme(themes, 'risques');
		const inond = risques.rowsCommune?.find((r) => r.fieldId === 'bien.risques.inondation');
		const seisme = risques.rowsCommune?.find((r) => r.fieldId === 'bien.risques.sismique');
		const argiles = risques.rowsBien?.find((r) => r.fieldId === 'bien.risques.expo_argiles');
		// Derived from risques_naturels[].risques_detail[].libelle_risque_long + argiles.exposition.
		expect(inond?.value).toBe('Oui');
		expect(seisme?.value).toBe('Oui');
		expect(argiles?.value).toBe('Exposition moyenne');
		// Real data present → NOT falsely degraded to "non disponible" (the WR-05 symptom).
		expect([inond, seisme, argiles].some((r) => r?.available === false)).toBe(false);
	});

	it("'absent': a never-requested dataset → available:false", () => {
		// monuments is never in datasets → absent → its rows degrade. We assert via a
		// dataset removed entirely: drop cadastre from the request.
		const noCadastre = {
			...mockResolve85,
			datasets: {
				dpe: mockResolve85.datasets!.dpe,
				dvf: mockResolve85.datasets!.dvf,
				risques: mockResolve85.datasets!.risques,
				urbanisme: mockResolve85.datasets!.urbanisme,
				commune: mockResolve85.datasets!.commune
			}
		};
		const { themes } = mapBienToFiche(noCadastre, COMMUNE_85047_CHALLANS);
		const propriete = theme(themes, 'propriete');
		const parcelleRow = (propriete.rowsBien ?? []).find((r) => /parcelle/i.test(r.label));
		expect(parcelleRow).toBeTruthy();
		expect(parcelleRow!.available).toBe(false);
	});
});

describe('mapBienToFiche() — honesty (never invents a value)', () => {
	it('on an all-empty envelope, every bien row is available:false', () => {
		const { themes } = mapBienToFiche(mockResolveAllEmpty, null);
		const allBienRows = themes.flatMap((t) => t.rowsBien ?? []);
		expect(allBienRows.length).toBeGreaterThan(0);
		expect(allBienRows.every((r) => r.available === false)).toBe(true);
	});
});

describe('mapBienToFiche() — split bien / commune', () => {
	it('with commune != null, at least one theme carries non-empty rowsCommune from mapCommuneToFiche', () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const withCommune = themes.filter((t) => (t.rowsCommune ?? []).length > 0);
		expect(withCommune.length).toBeGreaterThan(0);
		// The commune block reuses mapCommuneToFiche → e.g. Environnement carries population.
		const env = theme(themes, 'environnement');
		const popRow = (env.rowsCommune ?? []).find((r) => /population/i.test(r.label));
		expect(popRow).toBeTruthy();
		expect(popRow!.value).toContain('21');
	});

	it('without the commune synthesis, only the independent live commune risks remain', () => {
		const { themes } = mapBienToFiche(mockResolve85, null);
		const allCommuneRows = themes.flatMap((t) => t.rowsCommune ?? []);
		expect(allCommuneRows.filter((row) => row.kind !== 'context').map((row) => row.fieldId)).toEqual([
			'bien.risques.inondation', 'bien.risques.sismique'
		]);
		const allBienRows = themes.flatMap((t) => t.rowsBien ?? []);
		expect(allBienRows.length).toBeGreaterThan(0);
	});
});

describe('mapBienToFiche() — warnings (Pitfall 3)', () => {
	it('a dataset with ADRESSE_INCOMPLETE keeps its bien rows out of « sur ce bien »', () => {
		const { themes } = mapBienToFiche(mockResolveWithAvertissement, COMMUNE_85047_CHALLANS);
		// connectivite carries the warning → no connectivite-derived bien-level row appears.
		const allBienRows = themes.flatMap((t) => t.rowsBien ?? []);
		expect(
			allBienRows.some((r) => /connectivité|fibre|éligibilité/i.test(r.label) && r.available !== false)
		).toBe(false);
	});

	it('un avertissement ADRESSE_INCOMPLETE sur dpe ne décide plus rien ici — la décision est en amont', () => {
		// Phase 51 : l'avertissement disait « cette charge utile est de niveau commune ». Le DPE
		// n'a plus de charge utile à requalifier : sans numéro de voie il n'y a pas de clé BAN,
		// donc le backend répond `absent/identity_unavailable` et la sélection est vide. La
		// garantie survit, mais elle est STRUCTURELLE — vérifiée sur la décision, pas rejouée ici
		// à partir d'un avertissement que ce mapper pourrait oublier de lire.
		const warned = {
			...mockResolve85,
			avertissements: [
				{ dataset: 'dpe', code: 'ADRESSE_INCOMPLETE', message: 'Adresse incomplète' }
			]
		};
		const sansIdentite = ficheDepuisAttribution({
			status: 'absent',
			reason: 'identity_unavailable',
			diagnostic: null,
			candidates: [],
			building_context: null
		});
		expect(theme(sansIdentite.themes, 'energie').rowsBien!.every((r) => r.available === false)).toBe(
			true
		);
		// Et l'avertissement continue de dégrader les AUTRES datasets qu'il vise réellement.
		const { themes } = mapBienToFiche(warned, COMMUNE_85047_CHALLANS, selectionExacte());
		expect(theme(themes, 'marche').rowsBien!.some((r) => r.available !== false)).toBe(true);
	});
});

describe('mapBienToFiche() — numeric columns arrive as JSON strings (prod contract)', () => {
	// The resolve endpoint serializes Postgres `numeric` columns as JSON strings
	// ("168000.00"). If readNumber only accepted `typeof === 'number'`, every numeric
	// field fell to "non disponible" — the prod bug behind the empty Bien fiche.
	// The fixture uses raw numbers (frozen during the 530 outage), so this case feeds
	// strings explicitly to lock in the coercion.
	it('renders string-typed numeric DVF/DPE values instead of « non disponible »', () => {
		const dvf0 = (mockResolve85.datasets!.dvf as Record<string, unknown>[])[0];
		const stringy = {
			...mockResolve85,
			datasets: {
				...mockResolve85.datasets,
				dvf: [{ ...dvf0, valeur_fonciere: '168000.00', prix_m2: '2154' }]
			}
		} as unknown as typeof mockResolve85;
		// Le DPE ne transite plus par un dict brut : `DpePublicDiagnostic` est typé et Pydantic
		// sérialise ses `float` en nombres JSON. La coercition reste néanmoins exercée ici, en
		// défense en profondeur — un backend qui régresserait sur le typage ne doit toujours pas
		// faire disparaître la surface habitable derrière « non disponible ».
		const selection = selectionExacte({
			surface_habitable: '78' as unknown as number,
			conso_5_usages_ep: '412' as unknown as number
		});

		const { themes } = mapBienToFiche(stringy, COMMUNE_85047_CHALLANS, selection);

		const marche = theme(themes, 'marche');
		const vf = (marche.rowsBien ?? []).find((r) => /valeur foncière/i.test(r.label));
		expect(vf?.available).not.toBe(false);
		expect(vf?.value).toContain('168');

		const energie = theme(themes, 'energie');
		const surf = (energie.rowsBien ?? []).find((r) => /surface habitable/i.test(r.label));
		expect(surf?.available).not.toBe(false);
		expect(surf?.value).toContain('78');
	});

	it('L0 KPI prix au m² renders from a string-typed prix_m2', () => {
		// Phase 57 : le KPI lit la mutation de la DÉCISION, plus la première ligne du bloc DVF —
		// mais la coercition « numeric sérialisé en chaîne » doit survivre au changement de source.
		const decide = {
			...mockResolve85,
			dvf_attribution: compatible({ prix_m2: '2154' })
		} as unknown as typeof mockResolve85;
		const prixKpi = kpiDvf(mapBienToFiche(decide, COMMUNE_85047_CHALLANS, selectionExacte()));
		expect(prixKpi.value).not.toBe('n.d.');
		expect(prixKpi.value).toContain('2');
	});
});

describe('mapBienToFiche() — structure', () => {
	it('always returns exactly 5 themes with the stable keys', () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		expect(themes).toHaveLength(5);
		expect(themes.map((t) => t.key)).toEqual([
			'marche',
			'energie',
			'risques',
			'environnement',
			'propriete'
		]);
	});

	it('returns L0 KPI computed deterministically (dpe etiquette F → watch, never invented)', () => {
		const { kpi } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS, selectionExacte());
		expect(kpi.length).toBeGreaterThan(0);
		const dpeKpi = kpi.find((k) => /dpe|étiquette/i.test(k.label));
		expect(dpeKpi).toBeTruthy();
		expect(dpeKpi!.verdict).toBe('watch'); // F is a passoire → watch
		expect(dpeKpi!.verdictLabel).toBe('Passoire énergétique (classe F)');
	});

	it('on an all-empty envelope, all KPI fall back to info (never invented)', () => {
		const { kpi } = mapBienToFiche(mockResolveAllEmpty, null);
		expect(kpi.every((k) => k.verdict === 'info')).toBe(true);
		// Aucun POSITIONNEMENT n'est produit (D-07) : les KPI adossés à un référentiel publié
		// restent muets. Le KPI de prix, lui, porte désormais un ÉTAT dans la même pastille — pas
		// une comparaison —, et cet état est justement ce que la Phase 57 rend visible (D-16).
		expect(kpi.filter((k) => k.fieldId !== 'bien.dvf.prix_m2').every((k) => k.verdictLabel === undefined)).toBe(
			true
		);
	});
});

describe('mapBienToFiche() — DPE positioned on the published official scale (D-01)', () => {
	/** Rebuild the authorised selection with a chosen DPE label (or none at all). */
	function withEtiquette(etiquette: string | null) {
		return mapBienToFiche(
			mockResolve85,
			COMMUNE_85047_CHALLANS,
			selectionExacte({ etiquette_dpe: etiquette })
		).kpi[0];
	}

	it("classe C → bonne performance (good)", () => {
		expect(withEtiquette('C').verdict).toBe('good');
		expect(withEtiquette('C').verdictLabel).toBe('Bonne performance (classe C)');
	});

	it('classe E → performance moyenne (neutral)', () => {
		expect(withEtiquette('E').verdict).toBe('neutral');
		expect(withEtiquette('E').verdictLabel).toBe('Performance moyenne (classe E)');
	});

	it('classe G → passoire énergétique au sens de la loi (watch)', () => {
		expect(withEtiquette('G').verdict).toBe('watch');
		expect(withEtiquette('G').verdictLabel).toBe('Passoire énergétique (classe G)');
	});

	it('étiquette absente → info et AUCUN libellé (jamais un positionnement inventé)', () => {
		expect(withEtiquette(null).verdict).toBe('info');
		expect(withEtiquette(null).verdictLabel).toBeUndefined();
	});

	it('une étiquette en minuscule atteint la même entrée du référentiel (toUpperCase conservé)', () => {
		expect(withEtiquette('g').verdict).toBe('watch');
		expect(withEtiquette('g').verdictLabel).toBe('Passoire énergétique (classe G)');
	});

	it("l'écart cite l'échelle officielle et sa source, jamais un seuil maison", () => {
		const ecart = withEtiquette('G').ecartTexte ?? '';
		expect(ecart).toContain('1er juillet 2021');
		expect(ecart).toContain('Source :');
	});

	it('le KPI prix reste sans positionnement (D-07) mais garde sa clé ℹ️', () => {
		const { kpi } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const prix = kpiDvf({ kpi, themes: [] });
		expect(prix.verdict).toBe('info');
		expect(prix.fieldId).toBe('bien.dvf.prix_m2');
		// Aucun mot de comparaison : la pastille ne porte QUE l'état d'attribution (D-16).
		expect(prix.verdictLabel).not.toMatch(/médiane|référence|au-dessus|en dessous|proche/i);
	});

	it('le KPI parcelle est un identifiant pur : aucun fieldId (exclu D-15)', () => {
		const { kpi } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const parcelle = kpi.find((k) => k.label === 'Parcelle cadastrale')!;
		expect(parcelle.fieldId).toBeUndefined();
		expect(parcelle.verdictLabel).toBeUndefined();
	});
});

describe('mapBienToFiche() — métriques avec fieldId/state, contexte textuel explicite (D-18)', () => {
	it('la ligne DVF en régime de tranche masquée porte sa clé ℹ️ et state "value"', () => {
		// CR-01 (49-10): the fixture is PRODUCED by the real D-03 bucket function, never a
		// hand-written lookalike — so a unit drift in formatPriceBucket turns this test red.
		const bucket = formatPriceBucket('bien.dvf.prix_m2', 2154, 500);
		const dvf0 = (mockResolve85.datasets!.dvf as Record<string, unknown>[])[0];
		const masque = {
			...mockResolve85,
			datasets: {
				...mockResolve85.datasets,
				dvf: [{ ...dvf0, prix_m2: bucket }]
			}
		} as unknown as typeof mockResolve85;
		const { themes } = mapBienToFiche(masque, COMMUNE_85047_CHALLANS);
		const prixRow = (theme(themes, 'marche').rowsBien ?? []).find((r) => r.label === 'Prix au m²')!;
		expect(prixRow.value).toBe(bucket); // rendered verbatim, never degraded
		// The bracket must carry the SAME unit the ℹ️ announces (« Unité : €/m² »), CR-01.
		expect(String(prixRow.value).endsWith('€/m²')).toBe(true);
		expect(prixRow.fieldId).toBe('bien.dvf.prix_m2');
		expect(prixRow.state).toBe('value');
	});

	it('le KPI prix au m² en régime masqué rend la tranche avec son unité, sans la doubler (CR-01)', () => {
		const bucket = formatPriceBucket('bien.dvf.prix_m2', 2154, 500);
		// Phase 57 : la tranche masquée doit traverser la DÉCISION aussi. Le loader la pose sur les
		// deux copies de la mutation (D-03 étendu), et le KPI la rend telle quelle.
		const masque = {
			...mockResolve85,
			dvf_attribution: compatible({ prix_m2: bucket })
		} as unknown as typeof mockResolve85;
		const prix = kpiDvf(mapBienToFiche(masque, COMMUNE_85047_CHALLANS));
		expect(prix.value).toBe(bucket);
		expect(String(prix.value).endsWith('€/m²')).toBe(true);
		// The masked string already carries its unit — the separate slot must stay empty,
		// otherwise the chip would render « … €/m² €/m² ».
		expect(prix.unit).toBeUndefined();
	});

	it('une liste de risques sans preuve de complétude reste not_reported', () => {
		// The risques dataset answered, but with no usable risk label for this location.
		const sansLibelle = {
			...mockResolve85,
			datasets: { ...mockResolve85.datasets, risques: { risques_naturels: [] } }
		} as unknown as typeof mockResolve85;
		const { themes } = mapBienToFiche(sansLibelle, COMMUNE_85047_CHALLANS);
		const rows = theme(themes, 'risques').rowsCommune ?? [];
		const inondation = rows.find((r) => /inondation/i.test(r.label))!;
		expect(inondation.muted).toBe(true);
		expect(inondation.fieldId).toBe('bien.risques.inondation');
		expect(inondation.state).toBe('not_reported');
	});

	it('chaque métrique porte fieldId/state et seul le contexte textuel explicite est exempté', () => {
		for (const envelope of [mockResolve85, mockResolveAllEmpty, mockResolveErrorRisques]) {
			const { themes } = mapBienToFiche(envelope, COMMUNE_85047_CHALLANS);
			for (const row of themes.flatMap((t) => [...(t.rowsBien ?? []), ...(t.rowsCommune ?? [])])) {
				if (row.kind === 'context') {
					expect(row.fieldId).toBeUndefined();
					expect(['value', 'not_reported']).toContain(row.state);
					expect(row.value).toEqual(expect.any(String));
					expect(row.value.trim()).not.toBe('');
					continue;
				}
				expect(row.fieldId, `row "${row.label}" has no fieldId`).toBeTruthy();
				expect(row.state, `row "${row.label}" has no state`).toBeTruthy();
			}
		}
	});

	it('préserve la réserve de contexte des loyers historiques dans les lignes communales du bien', () => {
		const { themes } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		const contexte = theme(themes, 'marche').rowsCommune?.find(
			(row) => row.label === 'Contexte loyer maison'
		);
		expect(contexte).toMatchObject({ kind: 'context', state: 'not_reported', muted: true });
		expect(contexte?.value).toContain('Précisions inconnues : charges, bien type, trimestre, maille et qualité.');
		expect(contexte?.value).toContain('Ces précisions ne sont pas disponibles pour cette donnée.');
	});
});

describe('mapBienToFiche() — semantic contract coverage (D-03, T-43-07)', () => {
	it('never throws on the populated fixture: every bien.* field_id it references is registered', () => {
		expect(() => mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS)).not.toThrow();
	});

	it('never throws on an all-empty envelope (every gap-branch field_id is registered too)', () => {
		expect(() => mapBienToFiche(mockResolveAllEmpty, null)).not.toThrow();
	});

	it('never throws when a dataset is in erreurs[] (temporary_error branch field_ids registered)', () => {
		expect(() => mapBienToFiche(mockResolveErrorRisques, COMMUNE_85047_CHALLANS)).not.toThrow();
	});

	it('assertFieldsCovered() throws SemanticContractError on a mapper field_id gap (T-43-07 proof)', () => {
		// Demonstrates the guard mapBienToFiche() implicitly relies on: a mapper field
		// referencing an unregistered field_id fails loud instead of silently rendering.
		expect(() => assertFieldsCovered(['bien.this.field.was.never.registered'])).toThrow(
			SemanticContractError
		);
	});
});

// --- Phase 51 : le KPI et le thème ne lisent QUE la sélection autorisée (D-10/D-18) ----------

const CANDIDAT_A = {
	numero_dpe: 'CAND-A',
	etage: 1,
	batiment: null,
	porte: 'Porte gauche',
	surface_habitable: 48,
	etiquette_dpe: 'E',
	annee_diagnostic: 2022,
	perime: false
};
const CANDIDAT_B = { ...CANDIDAT_A, numero_dpe: 'CAND-B', etage: 3, etiquette_dpe: 'C' };

const ATTRIBUTIONS: Record<string, DpeAttribution> = {
	exact: { status: 'exact', reason: null, diagnostic: diagnostic(), candidates: [], building_context: null },
	multiple: {
		status: 'multiple',
		reason: null,
		diagnostic: null,
		candidates: [CANDIDAT_A, CANDIDAT_B],
		building_context: null
	},
	building: {
		status: 'building',
		reason: 'ambiguous_unresolved',
		diagnostic: null,
		candidates: [],
		building_context: { count: 7, distribution: { C: 2, D: 5 }, truncated: false }
	},
	absent: {
		version: 2,
		status: 'absent',
		reason: 'not_reported',
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: 'not_checked'
	},
	error: {
		version: 2,
		status: 'error',
		reason: 'temporary_error',
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: 'not_checked'
	}
};

/** The energy rows of a fiche, whatever the state. */
function lignesEnergie(fiche: ReturnType<typeof mapBienToFiche>) {
	return theme(fiche.themes, 'energie').rowsBien ?? [];
}

function kpiDpe(fiche: ReturnType<typeof mapBienToFiche>) {
	return fiche.kpi.find((k) => k.fieldId === 'bien.dpe.etiquette_dpe')!;
}

describe('contrat V2 : aucun document consulté ou payload legacy n’alimente le KPI', () => {
	it('un ancien exact est fermé sans promouvoir son diagnostic dans la fiche', () => {
		const fiche = ficheDepuisAttribution(ATTRIBUTIONS.exact);
		expect(kpiDpe(fiche).value).toBe('n.d.');
		expect(kpiDpe(fiche).dpeClass).toBeUndefined();
		expect(lignesEnergie(fiche).every((r) => r.available === false)).toBe(true);
	});

	it('une ancre legacy ne transforme plus un candidat multiple en DPE du logement', () => {
		const fiche = ficheDepuisAttribution(ATTRIBUTIONS.multiple, 'CAND-B');
		expect(kpiDpe(fiche).value).toBe('n.d.');
		expect(kpiDpe(fiche).dpeClass).toBeUndefined();
		expect(lignesEnergie(fiche).every((r) => r.available === false)).toBe(true);
	});

	for (const etat of ['multiple', 'building', 'absent', 'error'] as const) {
		it(`${etat} sans sélection : KPI n.d., aucune pastille, lignes Énergie indisponibles`, () => {
			const fiche = ficheDepuisAttribution(ATTRIBUTIONS[etat]);
			const kpi = kpiDpe(fiche);
			expect(kpi.value).toBe('n.d.');
			expect(kpi.dpeClass).toBeUndefined();
			expect(kpi.verdict).toBe('info');
			expect(kpi.verdictLabel).toBeUndefined();
			// Aucune « première ligne » arbitraire ne peut plus remplir le thème.
			expect(lignesEnergie(fiche).every((r) => r.available === false)).toBe(true);
		});
	}

	it('error se distingue d’une absence métier par l’état exact de ses lignes (D-13)', () => {
		const panne = lignesEnergie(ficheDepuisAttribution(ATTRIBUTIONS.error));
		const absence = lignesEnergie(ficheDepuisAttribution(ATTRIBUTIONS.absent));
		expect(panne.every((r) => r.state === 'temporary_error')).toBe(true);
		expect(absence.every((r) => r.state === 'out_of_coverage')).toBe(true);
		// Même rendu à l'écran, deux raisons différentes — c'est précisément ce que D-13 exige.
		expect(panne.map((r) => r.value)).toEqual(absence.map((r) => r.value));
	});

	it('une ancre étrangère ne modifie ni le KPI ni le thème (D-09, T-51-13)', () => {
		const sansAncre = ficheDepuisAttribution(ATTRIBUTIONS.multiple);
		const ancreEtrangere = ficheDepuisAttribution(ATTRIBUTIONS.multiple, 'DPE-DU-VOISIN');
		expect(ancreEtrangere.kpi).toEqual(sansAncre.kpi);
		expect(lignesEnergie(ancreEtrangere)).toEqual(lignesEnergie(sansAncre));
		expect(kpiDpe(ancreEtrangere).value).toBe('n.d.');
	});

	it('un candidat legacy, même présent dans la liste, ne fournit aucun champ à la fiche', () => {
		const fiche = ficheDepuisAttribution(ATTRIBUTIONS.multiple, 'CAND-A');
		const ges = lignesEnergie(fiche).find((r) => /étiquette ges/i.test(r.label))!;
		expect(ges.available).toBe(false);
		expect(
			lignesEnergie(fiche).find((r) => /surface habitable/i.test(r.label))!.available
		).toBe(false);
		expect(JSON.stringify(fiche)).not.toContain('CAND-A');
	});

	it('sans sélection explicite, la valeur par défaut est fermée : aucun DPE affiché', () => {
		expect(SELECTION_DPE_INDISPONIBLE.diagnostic).toBeNull();
		const { kpi } = mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS);
		expect(kpi.find((k) => k.fieldId === 'bien.dpe.etiquette_dpe')!.value).toBe('n.d.');
	});
});

describe('garde de source : le mapper ne peut plus interpréter un tableau DPE brut', () => {
	const source = readFileSync(joinPath(process.cwd(), 'src/lib/data/bien-fiche.ts'), 'utf8');
	// Le littéral traqué est reconstruit, jamais écrit tel quel : l'écrire ici le ferait
	// apparaître dans un fichier que d'autres gardes balaient.
	const LECTURE_INTERDITE = ['datasets', 'dpe'].join('.') + '[0]';

	it('ne lit plus le bloc DPE de la charge utile, ni sa première ligne', () => {
		expect(source.length).toBeGreaterThan(1000); // non-vacuité
		expect(source).not.toContain(LECTURE_INTERDITE);
		expect(source).not.toMatch(/datasets\?\.\s*dpe/);
		expect(source).not.toMatch(/asList\([^)]*dpe[^)]*\)/);
		// Aucune inférence depuis l'état grossier non plus : il vaut `value` en `multiple` comme
		// en `building`, donc le lire ici recréerait une seconde règle, forcément plus pauvre.
		expect(source).not.toContain('dataset_states');
	});

	it('les autres datasets conservent leur lecture de charge utile (aucune sur-correction)', () => {
		for (const dataset of ['dvf', 'cadastre', 'risques', 'urbanisme']) {
			expect(source, `${dataset} doit rester lu depuis la charge utile`).toContain(
				`datasets?.${dataset}`
			);
		}
	});
});

// --- Phase 57 : les cinq états du bloc DVF, tous lus sur la décision (D-16/D-37) --------------

describe('matrice DVF : le prix ne sort QUE d’un état positif, l’absence porte sa raison', () => {
	it('compatible parcelle : le prix est rendu ET dit qu’il n’est pas attribué au bien (D-37)', () => {
		const kpi = kpiDvf(mapBienToFiche(avecDecisionDvf(compatible()), COMMUNE_85047_CHALLANS));
		expect(kpi.value).toContain('2');
		expect(kpi.unit).toBe('€/m²');
		expect(kpi.verdict).toBe('info');
		expect(kpi.verdictLabel).toBe(COPIE_DVF.etats.compatible_parcelle.mention);
		expect(kpi.ecartTexte).toBe(COPIE_DVF.etats.compatible_parcelle.detail);
		// Le libellé du KPI n'affirme aucune attribution : plus de « dernière vente » (D-37).
		expect(kpi.label).not.toMatch(/dernière vente|vente de ce bien/i);
	});

	it('vente mixte : aucun prix, l’absence et sa raison tiennent sur une ligne (D-11)', () => {
		const kpi = kpiDvf(
			mapBienToFiche(
				avecDecisionDvf(nonAttribuable('derniere_vente_mixte')),
				COMMUNE_85047_CHALLANS
			)
		);
		expect(kpi.value).toBe('n.d.');
		expect(kpi.verdictLabel).toBe(COPIE_DVF.raisons.derniere_vente_mixte.mention);
		expect(kpi.verdictLabel!.split('\n')).toHaveLength(1);
	});

	it('vente d’ensemble : aucun prix au KPI, et la mutation reste étiquetée dans l’historique', () => {
		const decision = avecDecisionDvf(nonAttribuable('vente_ensemble', true));
		const fiche = mapBienToFiche(
			{
				...decision,
				datasets: {
					...decision.datasets,
					dvf: [
						{
							...(mockResolve85.datasets!.dvf as Record<string, unknown>[])[0],
							classe_mutation: 'ensemble_residentiel'
						}
					]
				}
			} as unknown as typeof mockResolve85,
			COMMUNE_85047_CHALLANS
		);
		expect(kpiDvf(fiche).value).toBe('n.d.');
		expect(kpiDvf(fiche).verdictLabel).toBe(COPIE_DVF.raisons.vente_ensemble.mention);
		const nature = (theme(fiche.themes, 'marche').rowsBien ?? []).find(
			(r) => r.fieldId === 'bien.dvf.classe_mutation'
		)!;
		expect(nature.value).toBe(COPIE_DVF.classes.ensemble_residentiel);
		expect(nature.available).not.toBe(false);
	});

	it('VEFA sans surface : aucun prix au m² rendu, et la non-calculabilité est dite (DVF-06)', () => {
		const kpi = kpiDvf(
			mapBienToFiche(avecDecisionDvf(nonAttribuable('vefa_sans_surface')), COMMUNE_85047_CHALLANS)
		);
		expect(kpi.value).toBe('n.d.');
		expect(kpi.verdictLabel).toBe(COPIE_DVF.raisons.vefa_sans_surface.mention);
		expect(kpi.ecartTexte).toContain("aucun prix au mètre carré n'est calculable");
	});

	it('aucune mutation : l’absence est nommée, jamais un prix emprunté', () => {
		const kpi = kpiDvf(
			mapBienToFiche(
				avecDecisionDvf({ status: 'absente', reason: 'aucune_mutation' }),
				COMMUNE_85047_CHALLANS
			)
		);
		expect(kpi.value).toBe('n.d.');
		expect(kpi.verdictLabel).toBe(COPIE_DVF.raisons.aucune_mutation.mention);
	});

	it('type de bien inconnu : l’état que /v1/resolve produit aujourd’hui, dit tel quel', () => {
		const kpi = kpiDvf(
			mapBienToFiche(avecDecisionDvf(nonAttribuable('type_bien_inconnu')), COMMUNE_85047_CHALLANS)
		);
		expect(kpi.value).toBe('n.d.');
		expect(kpi.verdictLabel).toBe(COPIE_DVF.raisons.type_bien_inconnu.mention);
	});

	it('panne : une indisponibilité technique n’est JAMAIS lue comme « aucune vente » (D-16)', () => {
		const panne = kpiDvf(
			mapBienToFiche(
				avecDecisionDvf({ status: 'error', reason: 'temporary_error' }),
				COMMUNE_85047_CHALLANS
			)
		);
		const absence = kpiDvf(
			mapBienToFiche(
				avecDecisionDvf({ status: 'absente', reason: 'aucune_mutation' }),
				COMMUNE_85047_CHALLANS
			)
		);
		expect(panne.value).toBe('n.d.');
		expect(panne.verdictLabel).not.toBe(absence.verdictLabel);
		expect(panne.verdictLabel).toBe(COPIE_DVF.raisons.temporary_error.mention);
	});

	it('décision absente de l’enveloppe : indisponibilité fermée, jamais un repli sur la liste', () => {
		// L'enveloppe porte pourtant un bloc DVF complet : si le KPI le lisait encore, il
		// afficherait 2 154 €/m². C'est exactement la substitution que cette phase retire.
		const fiche = mapBienToFiche(avecDecisionDvf(null), COMMUNE_85047_CHALLANS);
		expect(kpiDvf(fiche).value).toBe('n.d.');
		expect(kpiDvf(fiche).verdictLabel).toBe(COPIE_DVF.raisons.temporary_error.mention);
		// … et l'historique de parcelle, lui, reste publié (D-11/D-17).
		expect((theme(fiche.themes, 'marche').rowsBien ?? []).some((r) => r.available !== false)).toBe(
			true
		);
	});

	it('aucun état ne se déduit du remplissage de la liste : liste vide, décision positive', () => {
		const sansListe = {
			...avecDecisionDvf(compatible()),
			datasets: { ...mockResolve85.datasets, dvf: [] }
		} as unknown as typeof mockResolve85;
		const fiche = mapBienToFiche(sansListe, COMMUNE_85047_CHALLANS);
		// Le KPI suit la DÉCISION (prix rendu) ; les lignes de détail suivent la charge utile
		// (dégradées) — deux sources, deux rôles, aucune inférence de l'une vers l'autre.
		expect(kpiDvf(fiche).value).toContain('2');
		expect((theme(fiche.themes, 'marche').rowsBien ?? []).every((r) => r.available === false)).toBe(
			true
		);
	});

	it('l’historique liste chaque transaction antérieure avec son étiquette de classe (D-17)', () => {
		const dvf0 = (mockResolve85.datasets!.dvf as Record<string, unknown>[])[0];
		const avecHistorique = {
			...avecDecisionDvf(compatible()),
			datasets: {
				...mockResolve85.datasets,
				dvf: [
					dvf0,
					{ ...dvf0, id_mutation: '2019-1', date_mutation: '2019-04-02', classe_mutation: 'mixte' },
					{
						...dvf0,
						id_mutation: '2018-1',
						date_mutation: '2018-03-01',
						classe_mutation: 'residentiel_appartement',
						hors_stats_vefa: true
					}
				]
			}
		} as unknown as typeof mockResolve85;
		const rows = theme(
			mapBienToFiche(avecHistorique, COMMUNE_85047_CHALLANS).themes,
			'marche'
		).rowsBien!.filter((r) => r.label === COPIE_DVF.lignesBien.historique);
		expect(rows).toHaveLength(2);
		expect(rows[0].value).toContain(COPIE_DVF.classes.mixte);
		expect(rows[0].value).toContain('2019');
		// La vente sur plan est nommée pour ce qu'elle est, en plus de sa classe (D-12/D-18).
		expect(rows[1].value).toContain(COPIE_DVF.suffixeVefa);
		// D-46 : date et étiquette sont séparées par un point médian, jamais par un tiret
		// cadratin — et aucune ligne d'historique n'en porte plus.
		expect(rows[0].value).toMatch(/\d · /);
		expect(rows.every((r) => !r.value.includes('—'))).toBe(true);
		// Aucun montant : un prix global n'a pas de lecture au niveau du bien.
		expect(rows.every((r) => !/€/.test(r.value))).toBe(true);
	});
});

describe('garde de source : le KPI de prix ne peut plus lire la liste de mutations', () => {
	const source = readFileSync(joinPath(process.cwd(), 'src/lib/data/bien-fiche.ts'), 'utf8');
	const debutKpi = source.indexOf('function buildBienKpi');
	const finKpi = source.indexOf('function mapDpeRows');

	it('le corps de buildBienKpi ne référence plus le bloc DVF de la charge utile', () => {
		expect(debutKpi).toBeGreaterThan(0);
		expect(finKpi).toBeGreaterThan(debutKpi);
		const corps = source.slice(debutKpi, finKpi);
		expect(corps).not.toContain('datasets?.dvf');
		expect(corps).not.toContain("datasetState(multi, 'dvf')");
		expect(corps).toContain('dvf.mutation');
	});
});
