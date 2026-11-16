import { readFileSync } from 'node:fs';
import { resolve as joinPath } from 'node:path';
import { describe, it, expect } from 'vitest';
import { mapCommuneToFiche, ficheDepth } from './commune-fiche';
import { COPIE_DVF } from './dvf-attribution-view';
import { getExplication } from './explications';
import { assertFieldsCovered, SemanticContractError, formatField } from './formatters';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import type { CommuneSynthese } from '$lib/api/commune';
import type { ThemeModel } from '$lib/types/fiche';

describe('grid KPI qualification before formatted-text information loss', () => {
	it('carries each raw KPI state and registry-approved measured zero', () => {
		const fiche = mapCommuneToFiche({ ...COMMUNE_85047_CHALLANS, loyer_m2_appartement: null, zone_abc: null, taux_vacance: 0, revenu_median: 0 });
		expect(fiche.kpi.map(item => item.presentation)).toEqual([
			{ state: 'out_of_coverage' }, { state: 'out_of_coverage' },
			{ state: 'value', measuredZero: true }, { state: 'value', measuredZero: false }
		]);
		expect(fiche.kpi.map(item => item.value)).toEqual(['n.d.', 'n.d.', '0', '0']);
	});
});

/** A fully-null commune (everything but code_insee) to exercise the all-degraded path. */
const EMPTY_COMMUNE: CommuneSynthese = {
	code_insee: '00000',
	nom_commune: null,
	code_departement: null,
	code_region: null,
	population: null,
	nb_menages: null,
	nb_logements: null,
	nb_logements_vacants_recensement: null,
	densite: null,
	superficie: null,
	annee_demographie: null,
	revenu_median: null,
	taux_pauvrete: null,
	indice_gini: null,
	part_imposee: null,
	annee_revenus: null,
	zone_abc: null,
	zone_abc_reclassement: null,
	taux_vacance: null,
	nb_vacants_plus_2ans: null,
	annee_vacance: null,
	taux_tfpb: null,
	taux_th_rs: null,
	taux_teom: null,
	nom_epci: null,
	annee_fiscalite: null,
	loyer_m2_appartement: null,
	loyer_m2_maison: null,
	annee_loyers: null,
	prix_m2_median: null,
	nb_mutations_12m: null,
	dpe_passoires_pct: null,
	dpe_median: null,
	annee_construction_mediane: null,
	nb_equipements: null,
	nb_etablissements_scolaires: null,
	nb_monuments: null,
	nb_proprietaires_pm: null,
	surface_pm_ha: null,
	fibre_pct: null
};

describe('mapCommuneToFiche() — L0 KPI set', () => {
	const { kpi } = mapCommuneToFiche(COMMUNE_85047_CHALLANS);

	it('produces exactly 4 L0 KPI items', () => {
		expect(kpi).toHaveLength(4);
	});

	it('exposes the frozen KPI set in order: loyer, tension ABC, vacance, revenu', () => {
		expect(kpi.map((k) => k.label)).toEqual([
			"Loyer d'annonce prédit (appartement)",
			'Tension de marché',
			'Taux de vacance',
			'Revenu déclaré médian'
		]);
	});

	it('never labels the rent KPI as a sale price and keeps it info (no reliable per-m² benchmark)', () => {
		const loyer = kpi[0];
		expect(loyer.label.toLowerCase()).not.toContain('prix de vente');
		expect(loyer.label.toLowerCase()).not.toContain('vente');
		expect(loyer.verdict).toBe('info');
		expect(loyer.unit).toBe('€/m²/mois');
	});

	it('falls back to n.d. + info when the rent source field is null', () => {
		// fixture has loyer_m2_appartement = null
		const loyer = kpi[0];
		expect(loyer.value).toBe('n.d.');
		expect(loyer.verdict).toBe('info');
	});

	it('describes zone_abc B2 without positioning it (A-01: no published order)', () => {
		const tension = kpi[1];
		expect(tension.value).toBe('B2');
		expect(tension.verdict).toBe('info');
		expect(tension.verdictLabel).toBe('Marché intermédiaire (zone B2)');
	});

	it('shows the vacancy rate with NO comparison at all (D-07, LOVAC definition mismatch)', () => {
		const vacance = kpi[2];
		expect(vacance.unit).toBe('%');
		expect(vacance.value).toBe('9,4'); // fr-FR formatted (FR_NUMBER), like the theme rows
		expect(vacance.verdict).toBe('info');
		expect(vacance.verdictLabel).toBeUndefined();
	});

	it('positions revenu_median below the published national median (21 340 € vs 22 900 €)', () => {
		const revenu = kpi[3];
		expect(revenu.unit).toBe('€/UC/an');
		expect(revenu.verdict).toBe('watch');
		expect(revenu.verdictLabel).toBe('En dessous de la médiane');
	});

	it('yields >= 1 KPI positioned against a published referential (non-info)', () => {
		// Only a metric whose publication ORDERS its values can carry a positioning. Revenu is the
		// last one standing: loyer and vacance are D-07 honest silence, and the ABC zone is now
		// always `info` — the arrêté classifies market tension and publishes no order of
		// desirability (A-01). `>= 1` is a non-vacuity guard, never a frozen count.
		const computed = kpi.filter((k) => k.verdict !== 'info');
		expect(computed.length).toBeGreaterThanOrEqual(1);
	});

	it('every KPI carries its ℹ️ key, and only the compared ones carry an écart sentence', () => {
		expect(kpi.map((k) => k.fieldId)).toEqual([
			'loyers.loyer_m2_appartement',
			'zonage_abc.zone',
			'vacance.taux_vacance',
			'revenus.revenu_median'
		]);
		expect(kpi[0].ecartTexte).toBeFalsy(); // D-07 silence
		expect(kpi[2].ecartTexte).toBeFalsy(); // D-07 silence
		expect(kpi[1].ecartTexte).toContain('Source :');
		expect(kpi[3].ecartTexte).toContain('Source :');
	});
});

describe('mapCommuneToFiche() — positionings come from published referentials (D-01)', () => {
	function tension(zone: string | null) {
		return mapCommuneToFiche({ ...EMPTY_COMMUNE, zone_abc: zone }).kpi[1];
	}
	function vacance(taux: number | null) {
		return mapCommuneToFiche({ ...EMPTY_COMMUNE, taux_vacance: taux }).kpi[2];
	}
	function revenu(rev: number | null) {
		return mapCommuneToFiche({ ...EMPTY_COMMUNE, code_insee: '44109', annee_revenus: 2021, revenu_median: rev }).kpi[3];
	}

	// Referential: INSEE/Filosofi national median declared income = 22 900 € (2021), ±5 % band.
	it('revenu 25 000 € → above the median (good)', () => {
		expect(revenu(25000).verdict).toBe('good');
		expect(revenu(25000).verdictLabel).toBe('Au-dessus de la médiane');
	});

	it('revenu 23 000 € → inside the published ±5 % band (neutral)', () => {
		expect(revenu(23000).verdict).toBe('neutral');
		expect(revenu(23000).verdictLabel).toBe('Proche de la médiane');
	});

	it('revenu 20 000 € → below the median (watch)', () => {
		expect(revenu(20000).verdict).toBe('watch');
		expect(revenu(20000).verdictLabel).toBe('En dessous de la médiane');
	});

	it('revenu null → info and NO label (never a positioning on an absent value)', () => {
		expect(revenu(null).verdict).toBe('info');
		expect(revenu(null).verdictLabel).toBeUndefined();
	});

	it('the écart sentence cites the tolerance band and the source (D-06, no hidden threshold)', () => {
		const ecart = revenu(23000).ecartTexte ?? '';
		expect(ecart).toContain('±5 %');
		expect(ecart).toContain('Filosofi');
	});

	// Referential: the official A/B/C classification itself, rendered WITHOUT gradation (A-01) —
	// the arrêté publishes a tension classification, not an order of desirability.
	it('zone A and zone C are both described, never positioned (both info, labels intact)', () => {
		expect(tension('A').verdict).toBe('info');
		expect(tension('A').verdictLabel).toBe('Marché tendu (zone A)');
		expect(tension('C').verdict).toBe('info');
		expect(tension('C').verdictLabel).toBe('Marché détendu (zone C)');
	});

	it('zone null → info and NO label', () => {
		expect(tension(null).verdict).toBe('info');
		expect(tension(null).verdictLabel).toBeUndefined();
	});

	// D-07 verrou: the vacancy rate NEVER gets a positioning, whatever its value.
	it('vacance 12 % → info and NO verdictLabel (the old « > 8 % → watch » cut-off is gone)', () => {
		expect(vacance(12).verdict).toBe('info');
		expect(vacance(12).verdictLabel).toBeUndefined();
		expect(vacance(3.2).verdict).toBe('info');
		expect(vacance(3.2).verdictLabel).toBeUndefined();
		expect(vacance(null).verdict).toBe('info');
	});

	it('never invents a verdict on a null source field (all fallbacks are info)', () => {
		const { kpi } = mapCommuneToFiche(EMPTY_COMMUNE);
		expect(kpi.every((k) => k.verdict === 'info')).toBe(true);
		expect(kpi.every((k) => k.verdictLabel === undefined)).toBe(true);
		expect(kpi.every((k) => k.value === 'n.d.')).toBe(true);
	});
});

describe('mapCommuneToFiche() — every KvRow carries its ℹ️ key AND its exact state (D-18)', () => {
	it('a rendered value row carries fieldId + state "value"', () => {
		const { themes } = mapCommuneToFiche(COMMUNE_85047_CHALLANS);
		const env = themes.find((t) => t.key === 'environnement')!;
		const population = env.rows.find((r) => r.label === 'Population')!;
		expect(population.fieldId).toBe('demographie.population');
		expect(population.state).toBe('value');
	});

	it('a null row carries its gap state, distinct per reason (soft vs hard)', () => {
		const covered: CommuneSynthese = { ...EMPTY_COMMUNE, population: 21179 };
		const { themes } = mapCommuneToFiche(covered);
		const env = themes.find((t) => t.key === 'environnement')!;
		// superficie null on a covered commune → soft gap not_reported (DATA-03).
		const superficie = env.rows.find((r) => r.label === 'Superficie')!;
		expect(superficie.fieldId).toBe('demographie.superficie');
		expect(superficie.state).toBe('not_reported');
		// revenu null → default hard gap out_of_coverage.
		const revenu = env.rows.find((r) => r.label === 'Revenu déclaré médian')!;
		expect(revenu.state).toBe('out_of_coverage');
	});

	it('no row of any theme is ever emitted without a fieldId and a state', () => {
		for (const commune of [COMMUNE_85047_CHALLANS, EMPTY_COMMUNE]) {
			const { themes } = mapCommuneToFiche(commune);
			for (const theme of themes) {
				for (const row of theme.rows) {
					if (row.kind === 'context') {
						expect(row.fieldId).toBeUndefined();
						expect(['value', 'not_reported']).toContain(row.state);
						continue;
					}
					expect(row.fieldId, `row "${row.label}" has no fieldId`).toBeTruthy();
					expect(row.state, `row "${row.label}" has no state`).toBeTruthy();
				}
			}
		}
	});

	it('W-05: a value that fails its own formatter degrades to state "temporary_error", never "value"', () => {
		// The row RENDERS as a hard gap; keeping state 'value' would make the ℹ️ explain a figure
		// that was never displayed, with no reason given for its absence.
		const row = formatField('bien.dvf.valeur_fonciere', 'Valeur foncière', {
			state: 'value',
			value: 'not-a-number'
		});
		expect(row.value).toBe('non disponible');
		expect(row.available).toBe(false);
		expect(row.state).toBe('temporary_error');
		expect(row.fieldId).toBe('bien.dvf.valeur_fonciere');
	});
});

describe('mapCommuneToFiche() — 5 themes always present', () => {
	const { themes } = mapCommuneToFiche(COMMUNE_85047_CHALLANS);

	it('always renders exactly 5 themes (stable structure, D9-01)', () => {
		expect(themes).toHaveLength(5);
		expect(themes.map((t) => t.key)).toEqual([
			'marche',
			'energie',
			'risques',
			'environnement',
			'propriete'
		]);
	});

	it('renders the 5 themes even on a fully-null commune (no theme hiding)', () => {
		const { themes: emptyThemes } = mapCommuneToFiche(EMPTY_COMMUNE);
		expect(emptyThemes).toHaveLength(5);
	});

	it('exposes populated DPE coverage on Énergie & bâti (part F et G + classe médiane)', () => {
		const energie = themes.find((t) => t.key === 'energie')!;
		const passoires = energie.rows.find((r) => r.label === 'Part des classes F et G')!;
		const median = energie.rows.find((r) => r.label === 'Classe DPE médiane')!;
		expect(passoires.available).not.toBe(false);
		expect(median.value).toBe('D');
	});

	it('still degrades Énergie rows honestly when DPE fields are null (all-null commune)', () => {
		const { themes: emptyThemes } = mapCommuneToFiche(EMPTY_COMMUNE);
		const energie = emptyThemes.find((t) => t.key === 'energie')!;
		expect(energie.rows.some((r) => r.available === false)).toBe(true);
	});

	it('keeps the georisques risques naturels row a structural gap on Risques', () => {
		const risques = themes.find((t) => t.key === 'risques')!;
		expect(risques.rows.some((r) => r.available === false)).toBe(true);
	});

	it('wires populated bonus fields into covered themes (Environnement carries revenu/densite)', () => {
		const env = themes.find((t) => t.key === 'environnement')!;
		const available = env.rows.filter((r) => r.available !== false);
		expect(available.length).toBeGreaterThanOrEqual(2);
	});

	it('every theme carries a non-empty sources note', () => {
		expect(themes.every((t) => t.sources.length > 0)).toBe(true);
	});
});

describe('ficheDepth() — honest coverage counter', () => {
	it('counts filled indicators and distinct sources on the populated fixture', () => {
		// Hand-computed from the mapper against COMMUNE_85047_CHALLANS:
		//   marche 5 (loyer_m2_appartement null → excluded) + energie 5 + risques 3
		//   (georisques gap excluded) + environnement 11 + propriete 8 (copropriétés gap excluded) = 32.
		//   Sources: 14 distinct segments across the 5 themes ("Zonage ABC" shared by marche+risques
		//   counts once).
		const { themes } = mapCommuneToFiche(COMMUNE_85047_CHALLANS);
		expect(ficheDepth(themes)).toEqual({ indicateurs: 32, sources: 14 });
	});

	it('counts zero indicators but the static 14 sources on a fully-null commune', () => {
		// Every kvNumber/kvText degrades to available:false → no indicator counted; the sources
		// notes are static strings, independent of data → still 14.
		const { themes } = mapCommuneToFiche(EMPTY_COMMUNE);
		expect(ficheDepth(themes)).toEqual({ indicateurs: 0, sources: 14 });
	});

	it('dedups an exact-string source shared across themes ("Zonage ABC" counts once)', () => {
		const themes: ThemeModel[] = [
			{ key: 'a', emoji: '', name: 'A', hint: '', rows: [], sources: 'DVF, Zonage ABC' },
			{ key: 'b', emoji: '', name: 'B', hint: '', rows: [], sources: 'Zonage ABC, Vacance' }
		];
		expect(ficheDepth(themes).sources).toBe(3); // DVF, Zonage ABC, Vacance
	});

	it('excludes muted rows and structural gaps, counts only genuinely rendered rows', () => {
		const themes: ThemeModel[] = [
			{
				key: 'x',
				emoji: '',
				name: 'X',
				hint: '',
				rows: [
					{ label: 'kept', value: '1' },
					{ label: 'gap', value: 'non disponible', available: false },
					{ label: 'muted', value: 'Non renseigné', muted: true }
				],
				sources: 'S1'
			}
		];
		expect(ficheDepth(themes)).toEqual({ indicateurs: 1, sources: 1 });
	});
});

describe('mapCommuneToFiche() — semantic contract coverage (D-03, T-43-07)', () => {
	it('never throws on the populated fixture: every field_id it references is registered', () => {
		expect(() => mapCommuneToFiche(COMMUNE_85047_CHALLANS)).not.toThrow();
	});

	it('never throws on a fully-null commune (every gap/null branch field_id is registered too)', () => {
		expect(() => mapCommuneToFiche(EMPTY_COMMUNE)).not.toThrow();
	});

	it('assertFieldsCovered() throws SemanticContractError on a mapper field_id gap (T-43-07 proof)', () => {
		// Demonstrates the guard mapCommuneToFiche() implicitly relies on: a mapper field
		// referencing an unregistered field_id fails loud instead of silently rendering.
		expect(() => assertFieldsCovered(['this.field.was.never.registered'])).toThrow(
			SemanticContractError
		);
	});
});

describe('mapCommuneToFiche() — superficie/densité: soft gap not_reported sur commune couverte (DATA-03 gap 43-10)', () => {
	// A commune genuinely inside the 44/85 perimeter (population present) whose geometric
	// backfill (superficie/densité) has not run yet: the honest state is the soft gap
	// not_reported ("Non renseigné", muted), never the hard gap out_of_coverage.
	const COVERED_NO_GEOM: CommuneSynthese = {
		...EMPTY_COMMUNE,
		population: 21179,
		superficie: null,
		densite: null
	};

	function envRow(commune: CommuneSynthese, label: string) {
		const { themes } = mapCommuneToFiche(commune);
		const env = themes.find((t) => t.key === 'environnement')!;
		return env.rows.find((r) => r.label === label)!;
	}

	it('Test 1: superficie null sur commune couverte → muted "Non renseigné", jamais hard gap', () => {
		const superficie = envRow(COVERED_NO_GEOM, 'Superficie');
		expect(superficie.value).toBe('Non renseigné');
		expect(superficie.muted).toBe(true);
		expect(superficie.available).not.toBe(false);
	});

	it('Test 2: densité null sur commune couverte → muted "Non renseigné", jamais hard gap', () => {
		const densite = envRow(COVERED_NO_GEOM, 'Densité');
		expect(densite.value).toBe('Non renseigné');
		expect(densite.muted).toBe(true);
		expect(densite.available).not.toBe(false);
	});

	it('Test 3 (verrou de périmètre): population null reste un hard gap (ingestion CSV, pas backfill géométrique)', () => {
		const population = envRow({ ...EMPTY_COMMUNE, population: null }, 'Population');
		expect(population.value).toBe('non disponible');
		expect(population.available).toBe(false);
	});

	it('Test 4: superficie/densité avec une vraie valeur restent formatées (le fix ne casse pas le chemin nominal)', () => {
		const commune: CommuneSynthese = {
			...EMPTY_COMMUNE,
			population: 21179,
			superficie: 75.27,
			densite: 281.4
		};
		expect(envRow(commune, 'Superficie').value).toBe('75,27 km²');
		expect(envRow(commune, 'Densité').value).toBe('281,4 hab./km²');
	});
});

// --- Phase 57 : ventilation maison / appartement des statistiques DVF (D-14/D-18) ------------

describe('Fiche Commune — prix médian maison et appartement, chacun distinct (D-14)', () => {
	/** Les lignes DVF du thème Marché & prix, repérées par leur clé ℹ️ et jamais par un libellé. */
	function ligneDvf(commune: CommuneSynthese, fieldId: string) {
		const marche = mapCommuneToFiche(commune).themes.find((t) => t.key === 'marche')!;
		const row = marche.rows.find((r) => r.fieldId === fieldId);
		expect(row, `la ligne ${fieldId} doit exister`).toBeTruthy();
		return row!;
	}

	const VENTILEE: CommuneSynthese = {
		...COMMUNE_85047_CHALLANS,
		prix_m2_median_maison: 2480,
		nb_mutations_12m_maison: 156,
		prix_m2_median_appartement: 3120,
		nb_mutations_12m_appartement: 28
	};

	it('rend les deux médianes séparément, chacune avec son unité de registre', () => {
		expect(ligneDvf(VENTILEE, 'dvf.prix_m2_median_maison').value).toMatch(/^2.480 €\/m²$/);
		expect(ligneDvf(VENTILEE, 'dvf.prix_m2_median_appartement').value).toMatch(/^3.120 €\/m²$/);
		// La médiane globale reste publiée : la ventilation s'ajoute, elle ne remplace pas.
		expect(ligneDvf(VENTILEE, 'dvf.prix_m2_median').available).not.toBe(false);
	});

	it('ne substitue JAMAIS une médiane à l’autre : maison nulle reste n.d. (D9-01)', () => {
		const sansMaison: CommuneSynthese = { ...VENTILEE, prix_m2_median_maison: null };
		expect(ligneDvf(sansMaison, 'dvf.prix_m2_median_maison').value).toBe('non disponible');
		expect(ligneDvf(sansMaison, 'dvf.prix_m2_median_maison').available).toBe(false);
		// L'appartement, lui, garde sa valeur : la dégradation est par champ, pas par thème.
		expect(ligneDvf(sansMaison, 'dvf.prix_m2_median_appartement').value).toMatch(/^3.120 €\/m²$/);
	});

	it('l’effectif reste publié quand la médiane manque : il DIT pourquoi elle manque (D-23)', () => {
		const sousSeuil: CommuneSynthese = {
			...VENTILEE,
			prix_m2_median_appartement: null,
			nb_mutations_12m_appartement: 3
		};
		expect(ligneDvf(sousSeuil, 'dvf.prix_m2_median_appartement').available).toBe(false);
		expect(ligneDvf(sousSeuil, 'dvf.nb_mutations_12m_appartement').value).toBe('3');
		expect(ligneDvf(sousSeuil, 'dvf.nb_mutations_12m_appartement').available).not.toBe(false);
	});

	it('un effectif nul est une mesure, jamais un trou : zéro vente se dit zéro (D-04)', () => {
		const aucune: CommuneSynthese = {
			...VENTILEE,
			prix_m2_median_appartement: null,
			nb_mutations_12m_appartement: 0
		};
		expect(ligneDvf(aucune, 'dvf.nb_mutations_12m_appartement').value).toBe('0');
		expect(ligneDvf(aucune, 'dvf.nb_mutations_12m_appartement').available).not.toBe(false);
	});

	it('les trois lignes de prix portent la mention « hors ventes sur plan » (D-18)', () => {
		for (const fieldId of [
			'dvf.prix_m2_median',
			'dvf.prix_m2_median_maison',
			'dvf.prix_m2_median_appartement'
		]) {
			expect(ligneDvf(VENTILEE, fieldId).label, fieldId).toContain('hors ventes sur plan');
			// L'arbitrage D-19 (2026-08-25) a écarté l'acronyme du libellé visible : il ne doit plus
			// y revenir au fil d'un refactor.
			expect(ligneDvf(VENTILEE, fieldId).label, fieldId).not.toContain('VEFA');
		}
		// L'acronyme est développé dans l'info-bulle de chaque ligne, jamais laissé nu.
		expect(getExplication('dvf.prix_m2_median_maison').definition).toContain(
			"état futur d'achèvement"
		);
	});

	it('une commune sans ventilation dégrade honnêtement les quatre lignes ajoutées', () => {
		for (const fieldId of [
			'dvf.prix_m2_median_maison',
			'dvf.nb_mutations_12m_maison',
			'dvf.prix_m2_median_appartement',
			'dvf.nb_mutations_12m_appartement'
		]) {
			expect(ligneDvf(EMPTY_COMMUNE, fieldId).available, fieldId).toBe(false);
		}
	});

	it('toute la copie DVF de cette fiche vient de la constante provisoire, aucune chaîne dispersée', () => {
		const source = readFileSync(joinPath(process.cwd(), 'src/lib/data/commune-fiche.ts'), 'utf8');
		expect(source.length).toBeGreaterThan(1000); // non-vacuité
		for (const libelle of Object.values(COPIE_DVF.lignesCommune)) {
			expect(source, `« ${libelle} » ne doit pas être écrit en dur ici`).not.toContain(libelle);
		}
	});
});
