import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, IndicateursDpe, IndicateursDvf, SourceAttribution } from '$lib/api/commune';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './__fixtures__/commune-85191-20261012';
import { definitionPublique, getExplication } from './explications';
import { enClairChamp } from './explications-simples';
import { getFieldContract } from './formatters';
import { dateEnLettres, mapCommuneIndicators } from './commune-indicators';
import { AUCUNE_VENTE, communeKpis, fragmentsDeDates, PERIODE_ENTAMEE, periodeCourte, reperesConnus, reservesSynthese, TENDANCE_NON_PUBLIEE, type KpiCommune } from './commune-kpi';

// Flat columns as served by /v1/commune once D-47 typed them (03-06): a null column carries its
// declared state in `etats`. The dated blocks are null, as on an instance that does not serve
// them: the KPIs read the flat columns of the same meaning.
const FLAT: CommuneSynthese = {
	...COMMUNE_85047_CHALLANS,
	indicateurs_dvf: null,
	indicateurs_dpe: null,
	fenetre_mediane_dvf: '2024-12-31 → 2025-12-31',
	prix_m2_median_maison: 2480,
	nb_mutations_12m_maison: 96,
	prix_m2_median_appartement: 3120.5,
	nb_mutations_12m_appartement: 41
};

// The frozen 85191 answer of /v1/commune (2026-10-12), read as the page reads it.
const COMMUNE_85191 = COMMUNE_85191_OBSERVED as unknown as CommuneSynthese;
const SOURCES_85191 = COMMUNE_85191_SOURCES as unknown as SourceAttribution[];

function kpi(commune: CommuneSynthese, id: KpiCommune['id']): KpiCommune {
	const found = communeKpis(commune).find((item) => item.id === id);
	if (!found) throw new Error(`KPI ${id} absent`);
	return found;
}

const DVF_BLOCK: IndicateursDvf = {
	version_contrat: '1.1',
	version_population: 'fixture',
	date_calcul: '2026-08-03',
	generation: null,
	provenance: [{
		nom: 'DVF fixture', producteur: 'Fixture', licence: 'fixture', reference: 'https://example.org/dvf',
		date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown'
	}],
	ancrage_temporel: { etat: 'unknown', motif: 'Fixture : ancrage non qualifié.' },
	population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune',
	unite_prix: 'EUR/m2',
	methode_quantiles: 'percentile_cont',
	methode_variation: '100*(mediane_courante/mediane_precedente-1)',
	residentiel_maison: {
		precedente: { fenetre: { debut_inclus: '2024-01-01', fin_exclue: '2025-01-01' }, effectif: 3, mediane: { etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' }, quantiles: { etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' } },
		courante: { fenetre: { debut_inclus: '2025-01-01', fin_exclue: '2026-01-01' }, effectif: 4, mediane: { etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' }, quantiles: { etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' } },
		variation_pct: { etat: 'insufficient_sample', motif: 'Tendance non calculée.' }, qualification_tendance: null
	},
	residentiel_appartement: {
		precedente: { fenetre: { debut_inclus: '2024-01-01', fin_exclue: '2025-01-01' }, effectif: 20, mediane: { etat: 'value', valeur: 3000 }, quantiles: { etat: 'value', p10: 2500, p50: 3000, p90: 3500, qualification: 'fixture' } },
		courante: { fenetre: { debut_inclus: '2025-01-01', fin_exclue: '2026-01-01' }, effectif: 22, mediane: { etat: 'value', valeur: 3096 }, quantiles: { etat: 'value', p10: 2600, p50: 3096, p90: 3600, qualification: 'fixture' } },
		variation_pct: { etat: 'value', valeur: 3.2 }, qualification_tendance: 'fixture'
	}
};

describe('communeKpis — ① Prix médian des maisons (tracer)', () => {
	it('preserves the served inclusive window without changing the median or count', () => {
		const window = '2024-12-31 → 2025-12-31';
		const maisons = kpi({ ...FLAT, fenetre_mediane_dvf: window }, 'prix-maisons');
		// The card spells both served days, the end included (N-2); the panel keeps the served form.
		expect(maisons.lignes[0].fenetre).toBe('du 31 décembre 2024 au 31 décembre 2025');
		const periodEntry = maisons.volet.find((entry) => entry.terme === 'Période');
		expect(periodEntry && 'valeur' in periodEntry ? periodEntry.valeur : null).toBe(window);
		expect(maisons.lignes[0].etat).toEqual({ kind: 'valeur', texte: '2\u202f480', unite: '€/m²' });
		expect(maisons.lignes[0].effectif).toBe('96\u00a0ventes');
	});

	it('names an unknown measurement period instead of inventing rolling months', () => {
		const maisons = kpi({ ...FLAT, fenetre_mediane_dvf: null }, 'prix-maisons');
		expect(maisons.lignes[0].fenetre).toBe('Période de mesure non renseignée');
		expect(maisons.lignes[0].brut.valeur).toBe('2480');
	});

	it('keeps the served window when a small sample withholds the price', () => {
		const window = '2024-12-31 → 2025-12-31';
		const maisons = kpi({
			...FLAT,
			fenetre_mediane_dvf: window,
			prix_m2_median_maison: null,
			nb_mutations_12m_maison: 3,
			etats: { prix_m2_median_maison: { etat: 'insufficient_sample', motif: '3 ventes, seuil de publication : 5.' } }
		}, 'prix-maisons');
		expect(maisons.lignes[0].fenetre).toBe('du 31 décembre 2024 au 31 décembre 2025');
		expect(maisons.volet).toContainEqual({ terme: 'Période', valeur: window });
		expect(maisons.lignes[0].etat.kind).toBe('absence');
		expect(maisons.lignes[0].effectif).toBe('3\u00a0ventes');
	});

	it('opens the synthesis with the median price of houses', () => {
		const [premier] = communeKpis(FLAT);
		expect(premier.id).toBe('prix-maisons');
		expect(premier.rang).toBe(1);
		// The label of its theme row: the arbitrated one of a flat median (one field, one label).
		expect(premier.libelle).toBe('Prix médian maison (hors ventes sur plan)');
		expect(communeKpis(BLOCKS)[0].libelle).toBe('Prix médian des maisons');
	});

	it('renders the served value at the contract precision, with its size, window and source unit', () => {
		const maisons = kpi({ ...FLAT, prix_m2_median_maison: 2480.4 }, 'prix-maisons');
		const [ligne] = maisons.lignes;
		expect(ligne.fieldId).toBe('dvf.prix_m2_median_maison');
		// fraction_digits 0 in the contract: 2 480,4 is displayed 2 480, never recomputed.
		expect(ligne.etat).toEqual({ kind: 'valeur', texte: '2\u202f480', unite: '€/m²' });
		expect(ligne.effectif).toBe('96\u00a0ventes');
		expect(ligne.fenetre).toBe('du 31 décembre 2024 au 31 décembre 2025');
		expect(ligne.brut).toEqual({ fieldId: 'dvf.prix_m2_median_maison', valeur: '2480.4', etat: { kind: 'valeur' } });
	});

	it('keeps insufficient_sample with the real size, never a 0', () => {
		const commune: CommuneSynthese = {
			...FLAT,
			prix_m2_median_maison: null,
			nb_mutations_12m_maison: 3,
			etats: { prix_m2_median_maison: { etat: 'insufficient_sample', motif: '3 ventes, seuil de publication : 5.' } }
		};
		const [ligne] = kpi(commune, 'prix-maisons').lignes;
		expect(ligne.etat).toEqual({ kind: 'absence', etat: 'insufficient_sample', motif: '3 ventes, seuil de publication : 5.', effectif: 3 });
		expect(ligne.effectif).toBe('3\u00a0ventes');
		expect(ligne.brut.valeur).toBe('null');
		expect(JSON.stringify(ligne)).not.toMatch(/"texte":"0"/);
	});

	it('keeps out_of_coverage with its reason, and no window', () => {
		const commune: CommuneSynthese = {
			...FLAT,
			prix_m2_median_maison: null,
			nb_mutations_12m_maison: null,
			etats: {
				prix_m2_median_maison: { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' },
				nb_mutations_12m_maison: { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' }
			}
		};
		const maisons = kpi(commune, 'prix-maisons');
		const [ligne] = maisons.lignes;
		expect(ligne.etat).toEqual({ kind: 'absence', etat: 'out_of_coverage', motif: 'territoire_hors_perimetre', effectif: null });
		expect(ligne.effectif).toBeNull();
		expect(ligne.fenetre).toBeNull();
		expect(maisons.volet.find((entree) => entree.terme === 'État et motif')).toBeTruthy();
	});

	it('does not render an absence the API did not declare (API defect, never « Non renseigné »)', () => {
		const commune: CommuneSynthese = { ...FLAT, prix_m2_median_maison: null, etats: null };
		expect(kpi(commune, 'prix-maisons').lignes).toEqual([]);
	});

	it('reads the dated block when the instance serves it, with its state and window', () => {
		const maisons = kpi({ ...FLAT, indicateurs_dvf: DVF_BLOCK }, 'prix-maisons');
		const [ligne] = maisons.lignes;
		expect(ligne.id).toBe('dvf-maison-mediane-courante');
		expect(ligne.fieldId).toBe('dvf.indicateurs.maison.mediane_courante');
		expect(ligne.etat).toEqual({ kind: 'absence', etat: 'insufficient_sample', motif: 'Moins de 5 ventes.', effectif: 4 });
		// The line under the value: the short period, one calendar year (03-17 R-1 option (b)); the
		// panel keeps the exact served form.
		expect(ligne.fenetre).toBe('2025');
		expect(maisons.volet).toContainEqual({ terme: 'Période', valeur: '2025-01-01 → 2026-01-01 (fin exclue)' });
		expect(maisons.source).toEqual({ nom: 'DVF fixture', url: 'https://example.org/dvf' });
	});

	it('says in the panel why there is no median when the window holds no sale (03-17 F56)', () => {
		const vide = structuredClone(DVF_BLOCK);
		vide.residentiel_maison.courante = { ...vide.residentiel_maison.courante, effectif: 0, mediane: { etat: 'insufficient_sample', motif: 'Aucune vente.' } };
		const [ligne] = kpi({ ...FLAT, indicateurs_dvf: vide }, 'prix-maisons').lignes;
		expect(ligne.etat).toEqual({ kind: 'absence', etat: 'insufficient_sample', motif: 'Aucune vente.', effectif: 0, aide: AUCUNE_VENTE });
		expect(AUCUNE_VENTE).toBe('Aucune vente sur la période\u00a0: pas de prix médian.');
		// With sales, the glossary sentence of the state stays: no interface help is added.
		expect(kpi({ ...FLAT, indicateurs_dvf: DVF_BLOCK }, 'prix-maisons').lignes[0].etat).not.toHaveProperty('aide');
	});

	it('orders the source panel as the UI-SPEC and defines the value by its public definition (03-17 F66)', () => {
		const maisons = kpi({ ...FLAT, indicateurs_dvf: DVF_BLOCK }, 'prix-maisons');
		expect(maisons.volet.map((entree) => entree.terme)).toEqual([
			'En clair', 'Définition', 'Objet', 'Période', 'Unité', 'Source', 'Dernière importation réussie',
			'Méthode', 'Effectif', 'État et motif', 'Limites'
		]);
		// Owner review of 09/11: the plain sentence opens the panel, the public definition follows it.
		expect(maisons.volet[0]).toEqual({ terme: 'En clair', valeur: enClairChamp('dvf.indicateurs.maison.mediane_courante') });
		expect(maisons.volet[1]).toEqual({
			terme: 'Définition',
			valeur: getExplication('dvf.indicateurs.maison.mediane_courante').definition
		});
		expect(maisons.volet[1]).toEqual({ terme: 'Définition', valeur: definitionPublique('dvf.indicateurs.maison.mediane_courante') });
		expect(maisons.volet[1]).not.toEqual({
			terme: 'Définition',
			valeur: getFieldContract('dvf.indicateurs.maison.mediane_courante').description
		});
	});
});

const DPE_BLOCK: IndicateursDpe = {
	version_contrat: '1.0', version_population: 'fixture', date_calcul: '2026-08-03', generation: null,
	provenance: [{ nom: 'DPE fixture', producteur: 'Fixture', licence: 'fixture', reference: 'https://example.org/dpe', date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown' }],
	population: 'diagnostics_canoniques_disponibles_hors_traces_retirees', periode: 'bornes_etablissement_observees_non_exhaustives',
	date_evaluation: '2026-08-01', methode_mediane: 'percentile_disc', methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G',
	resultat: {
		etat: 'value',
		population: { total: 6, classes_connues: 5, types_batiment: { maison: 2, appartement: 2, autre: 1, inconnu: 1 }, validite: { unknown: 1, expired: 1, not_expired_by_date: 4, inconsistent_dates: 0, future_establishment: 0 }, remplacement: { not_checked: 5, no_successor_known: 1, replaced: 0, ambiguous: 0 }, regimes: { existant: 4, neuf: 1, inconnu: 1 }, source_inconnue: 0, revision_non_verifiee: 5 },
		distribution: { classes: { A: 0, B: 0, C: 2, D: 1, E: 2, F: 0, G: 0 }, inconnue: 1 },
		modes: ['C', 'E'], mediane_ordinale: 'D', part_fg_pct: { etat: 'value', valeur: 0 },
		date_etablissement_min: '2020-01-01', date_etablissement_max: '2026-07-31', dates_etablissement_inconnues: 1
	}
};

const BLOCKS: CommuneSynthese = { ...FLAT, indicateurs_dvf: DVF_BLOCK, indicateurs_dpe: DPE_BLOCK };

describe('communeKpis — la synthèse complète dans l’ordre validé (D-46)', () => {
	it('always returns the four KPIs in the order ① ② ③ ④', () => {
		for (const commune of [FLAT, BLOCKS]) {
			expect(communeKpis(commune).map((item) => [item.rang, item.id])).toEqual([
				[1, 'prix-maisons'], [2, 'prix-appartements'], [3, 'tendance'], [4, 'dpe-dominant']
			]);
		}
	});

	it('② reads the flats exactly like the houses', () => {
		const [ligne] = kpi(FLAT, 'prix-appartements').lignes;
		expect(ligne.fieldId).toBe('dvf.prix_m2_median_appartement');
		expect(ligne.etat).toEqual({ kind: 'valeur', texte: '3\u202f121', unite: '€/m²' });
		expect(ligne.effectif).toBe('41\u00a0ventes');
	});

	it('keeps two KPIs of the same value as two distinct slots', () => {
		const pareil = { ...FLAT, prix_m2_median_maison: 2500, prix_m2_median_appartement: 2500 };
		const [maisons, appartements] = communeKpis(pareil);
		expect(maisons.id).not.toBe(appartements.id);
		expect(maisons.lignes[0].etat).toEqual(appartements.lignes[0].etat);
	});
});

describe('communeKpis — ③ tendance', () => {
	it('renders two lines, houses then flats, from the served variation_pct (never recomputed)', () => {
		const tendance = kpi(BLOCKS, 'tendance');
		expect(tendance.lignes.map((ligne) => [ligne.id, ligne.libelle])).toEqual([
			['dvf-maison-variation', 'Maisons'],
			['dvf-appartement-variation', 'Appartements']
		]);
		const [maisons, appartements] = tendance.lignes;
		expect(maisons.etat).toEqual({ kind: 'absence', etat: 'insufficient_sample', motif: 'Tendance non calculée.', effectif: 4 });
		// 3096 / 3000 - 1 = 3.2 %: the served 3.2 is displayed, the medians are not divided here.
		expect(appartements.etat).toEqual({ kind: 'valeur', texte: '+3,2', unite: '%' });
		// Bare qualification, read in the panel only (03-17 F28).
		expect(appartements.qualification).toBe('fixture');
		expect(tendance.volet).toContainEqual({ terme: 'Qualification', valeur: 'fixture' });
		// Under the value, the sizes and the two short periods; the exact windows are read in the
		// panel (03-17 R-1 option (b)).
		expect(appartements.fenetre).toBe('2024 → 2025');
		expect(tendance.volet).toContainEqual({
			terme: 'Période', valeur: '2024-01-01 → 2025-01-01 (fin exclue) / 2025-01-01 → 2026-01-01 (fin exclue)'
		});
		expect(appartements.effectif).toBe('20 puis 22\u00a0ventes');
	});

	it('displays a served variation even when the medians would give another figure', () => {
		const block: IndicateursDvf = { ...DVF_BLOCK, residentiel_appartement: { ...DVF_BLOCK.residentiel_appartement, variation_pct: { etat: 'value', valeur: -1.25 } } };
		const [, appartements] = kpi({ ...BLOCKS, indicateurs_dvf: block }, 'tendance').lignes;
		expect(appartements.etat).toMatchObject({ kind: 'valeur', unite: '%' });
		expect((appartements.etat as { texte: string }).texte).toMatch(/^[-−]1,3$/);
	});

	it('keeps its two slots when the instance serves no dated block', () => {
		const tendance = kpi(FLAT, 'tendance');
		expect(tendance.lignes).toHaveLength(2);
		for (const ligne of tendance.lignes) {
			expect(ligne.etat).toMatchObject({ kind: 'absence', etat: 'not_available' });
		}
	});

	it('words the no-block fallback with the site reason, never with the glossary sentence', () => {
		for (const ligne of kpi(FLAT, 'tendance').lignes) {
			// « La source ne publie pas… » would blame DVF: here it is this instance that serves no trend.
			expect(ligne.etat).toEqual({
				kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE, effectif: null, aide: TENDANCE_NON_PUBLIEE
			});
		}
		// Plain words: no « datée », no « servi » (03-17 F31).
		expect(TENDANCE_NON_PUBLIEE).toBe("Ce site ne publie pas l'évolution des prix pour cette commune.");
		expect(TENDANCE_NON_PUBLIEE).not.toMatch(/servi|datée/);
		// A served block keeps the glossary help: no interface sentence is added to it.
		for (const ligne of kpi({ ...FLAT, indicateurs_dvf: DVF_BLOCK }, 'tendance').lignes) {
			expect(ligne.etat).not.toHaveProperty('aide');
		}
	});
});

// The model keeps the trend this site does not publish, with its state, reason and panel: the page drops
// the tile (`reperesConnus`) and reads them on the row of Immobilier (4.9, A1).
describe('communeKpis — tendance non publiée (04.5-03, 4.9)', () => {
	const etatEtMotif = (item: KpiCommune) => {
		const entree = item.volet.find((e) => e.terme === 'État et motif');
		return entree && 'absences' in entree ? entree.absences : [];
	};

	it('the trend of the frozen 85191 answer (no dated block) keeps its reason in the panel', () => {
		const tendance = communeKpis(COMMUNE_85191, SOURCES_85191).find((item) => item.id === 'tendance')!;
		expect(tendance.lignes.map((ligne) => ligne.etat)).toEqual([
			{ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE, effectif: null, aide: TENDANCE_NON_PUBLIEE },
			{ kind: 'absence', etat: 'not_available', motif: TENDANCE_NON_PUBLIEE, effectif: null, aide: TENDANCE_NON_PUBLIEE }
		]);
		expect(etatEtMotif(tendance).map((absence) => [absence.libelle, absence.etat.etat, absence.etat.motif])).toEqual([
			['Maisons', 'not_available', TENDANCE_NON_PUBLIEE],
			['Appartements', 'not_available', TENDANCE_NON_PUBLIEE]
		]);
	});

	it('a dated block served as an absence is not published either: its served state kept', () => {
		const tendance = kpi({ ...FLAT, indicateurs_dvf: { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' } as unknown as IndicateursDvf }, 'tendance');
		for (const ligne of tendance.lignes) expect(ligne.etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage' });
	});
});

describe('communeKpis — ④ DPE dominant', () => {
	it('gives the served modes in A→G order, the ordinal median and « sur {n} diagnostics »', () => {
		const dpe = kpi(BLOCKS, 'dpe-dominant');
		const [modes, mediane] = dpe.lignes;
		expect(modes.id).toBe('dpe-modes');
		// The title of the tile is the label of the theme row; only the median has a sub-label.
		expect(dpe.libelle).toBe('Classes les plus fréquentes, ex aequo');
		expect(modes.libelle).toBeNull();
		expect(modes.classes).toEqual(['C', 'E']);
		expect(modes.effectif).toBe('sur 5\u00a0diagnostics de classe connue');
		expect(mediane.id).toBe('dpe-mediane');
		expect(mediane.libelle).toBe('Classe DPE médiane');
		expect(mediane.classes).toEqual(['D']);
		expect(dpe.source).toEqual({ nom: 'DPE fixture', url: 'https://example.org/dpe' });
	});

	it('never invents a class: no known class is the served zero_denominator state', () => {
		const block: IndicateursDpe = {
			...DPE_BLOCK,
			resultat: { ...(DPE_BLOCK.resultat as Extract<IndicateursDpe['resultat'], { etat: 'value' }>), modes: [], mediane_ordinale: null, part_fg_pct: { etat: 'zero_denominator', motif: 'Aucune classe DPE connue.' } }
		};
		const dpe = kpi({ ...BLOCKS, indicateurs_dpe: block }, 'dpe-dominant');
		expect(dpe.lignes.length).toBeGreaterThan(0);
		for (const ligne of dpe.lignes) {
			expect(ligne.classes ?? []).toEqual([]);
			expect(ligne.etat).toEqual({ kind: 'absence', etat: 'zero_denominator', motif: 'Aucune classe DPE connue.', effectif: null });
		}
	});

	it('reads the flat median class when the instance serves no dated block', () => {
		const dpe = kpi({ ...FLAT, dpe_median: 'D' }, 'dpe-dominant');
		expect(dpe.lignes.map((ligne) => [ligne.fieldId, ligne.classes])).toEqual([['dpe.dpe_median', ['D']]]);
	});

	it('is titled by what is served: « Classe DPE médiane » on the flat column, the most frequent class with the modes (03-17 V27, F36)', () => {
		const repli = kpi({ ...FLAT, dpe_median: 'D' }, 'dpe-dominant');
		expect(repli.libelle).toBe('Classe DPE médiane');
		expect(repli.lignes[0].libelle).toBeNull();
		expect(repli.lignes[0].fenetre).toBe('Dates des diagnostics non renseignées');
		expect(repli.lignes[0].effectif).toBe('Effectif non renseigné');
		expect(kpi(BLOCKS, 'dpe-dominant').libelle).toBe('Classes les plus fréquentes, ex aequo');
	});
});

describe('periodeCourte — the short period of a served window (03-17 F26, R-1 option (b))', () => {
	it('is the year alone when the served window is exactly one calendar year', () => {
		expect(periodeCourte({ debut_inclus: '2025-01-01', fin_exclue: '2026-01-01' })).toBe('2025');
	});

	it('never rounds to a year: twelve months that are not a calendar year stay month to month', () => {
		expect(periodeCourte({ debut_inclus: '2023-03-01', fin_exclue: '2024-03-01' })).toBe('mars 2023 – février 2024');
		expect(periodeCourte({ debut_inclus: '2024-07-01', fin_exclue: '2025-07-01' })).toBe('juillet 2024 – juin 2025');
		expect(periodeCourte({ debut_inclus: '2025-01-01', fin_exclue: '2027-01-01' })).toBe('janvier 2025 – décembre 2026');
		expect(periodeCourte({ debut_inclus: '2025-02-01', fin_exclue: '2025-03-01' })).toBe('février 2025');
	});

	it('keeps the served days when the window does not follow whole months', () => {
		expect(periodeCourte({ debut_inclus: '2025-01-15', fin_exclue: '2026-01-15' })).toBe('du 15 janvier 2025 au 14 janvier 2026');
		expect(periodeCourte({ debut_inclus: '2025-01-01', fin_exclue: '2025-12-31' })).toBe('du 1er janvier 2025 au 30 décembre 2025');
	});

	it('keeps the served form when a date cannot be read', () => {
		expect(periodeCourte({ debut_inclus: 'x', fin_exclue: '2026-01-01' })).toBe('x → 2026-01-01 (fin exclue)');
	});
});

// N-2 (plan 04.5-03): no raw ISO day on the face of a landmark; the face spells the served days, the
// Source panel keeps the exact served form. The two served contracts stay apart: the flat
// `fenetre_mediane_dvf` has BOTH bounds included (API schema, C29), a dated block window
// `{ debut_inclus, fin_exclue }` excludes its end. A day is subtracted for the second one only.
const ISO = /\d{4}-\d{2}-\d{2}/;

/** What a card shows (03-17 R-1): title, sub-labels, values, sizes and periods; never its panel. */
function face(item: KpiCommune): string[] {
	return [
		item.libelle,
		...item.lignes.flatMap((ligne) => [ligne.libelle, ligne.etat.kind === 'valeur' ? ligne.etat.texte : null, ligne.effectif, ligne.fenetre])
	].filter((texte): texte is string => Boolean(texte));
}

function periode(item: KpiCommune): string | null {
	const entree = item.volet.find((e) => e.terme === 'Période');
	return entree && 'valeur' in entree ? entree.valeur : null;
}

describe('communeKpis — N-2 dates des repères en toutes lettres (04.5-03)', () => {
	it('no face of the four landmarks of the frozen 85191 answer carries an ISO day', () => {
		for (const item of communeKpis(COMMUNE_85191, SOURCES_85191)) {
			for (const texte of face(item)) expect(texte, item.id).not.toMatch(ISO);
		}
	});

	it('the panels of the frozen 85191 answer keep the exact served window', () => {
		const [maisons, appartements] = communeKpis(COMMUNE_85191, SOURCES_85191);
		expect(periode(maisons)).toBe('2024-12-31 → 2025-12-31');
		expect(periode(appartements)).toBe('2024-12-31 → 2025-12-31');
	});

	it('a flat inclusive window keeps its served end on the face, and the panel adds no « fin exclue »', () => {
		const maisons = kpi({ ...FLAT, fenetre_mediane_dvf: '2024-12-31 → 2025-12-31' }, 'prix-maisons');
		expect(maisons.lignes[0].fenetre).toBe('du 31 décembre 2024 au 31 décembre 2025');
		expect(periode(maisons)).toBe('2024-12-31 → 2025-12-31');
	});

	it('a dated block window excludes its served end: the face ends the day before, the panel says « fin exclue »', () => {
		const fenetre = { debut_inclus: '2024-12-31', fin_exclue: '2025-12-31' };
		const block: IndicateursDvf = {
			...DVF_BLOCK,
			residentiel_maison: { ...DVF_BLOCK.residentiel_maison, courante: { ...DVF_BLOCK.residentiel_maison.courante, fenetre } }
		};
		const maisons = kpi({ ...FLAT, indicateurs_dvf: block }, 'prix-maisons');
		expect(maisons.lignes[0].fenetre).toBe('du 31 décembre 2024 au 30 décembre 2025');
		expect(periode(maisons)).toBe('2024-12-31 → 2025-12-31 (fin exclue)');
	});

	it('a served label that is not a window of days is kept as served', () => {
		const maisons = kpi({ ...FLAT, fenetre_mediane_dvf: 'Période de la source' }, 'prix-maisons');
		expect(maisons.lignes[0].fenetre).toBe('Période de la source');
	});
});

describe('communeKpis — vide', () => {
	it('keeps four slots with their served states for a covered commune without DVF nor DPE value', () => {
		const vide: CommuneSynthese = {
			...FLAT,
			prix_m2_median_maison: null, nb_mutations_12m_maison: null,
			prix_m2_median_appartement: null, nb_mutations_12m_appartement: null,
			dpe_median: null,
			etats: {
				prix_m2_median_maison: { etat: 'not_reported', motif: 'Aucune vente.' },
				nb_mutations_12m_maison: { etat: 'not_reported', motif: 'Aucune vente.' },
				prix_m2_median_appartement: { etat: 'not_reported', motif: 'Aucune vente.' },
				nb_mutations_12m_appartement: { etat: 'not_reported', motif: 'Aucune vente.' },
				dpe_median: { etat: 'not_reported', motif: 'Aucun diagnostic.' }
			}
		};
		const kpis = communeKpis(vide);
		expect(kpis).toHaveLength(4);
		for (const item of kpis) {
			expect(item.lignes.length, item.id).toBeGreaterThan(0);
			for (const ligne of item.lignes) expect(ligne.etat.kind, `${item.id}/${ligne.id}`).toBe('absence');
		}
		expect(JSON.stringify(kpis)).not.toMatch(/"texte":"0"/);
	});
});

describe('reservesSynthese — ce qui mérite une lecture attentive', () => {
	it('keeps at most four caveats, each anchored to a value of the synthesis, never twice the same', () => {
		const reserves = reservesSynthese(BLOCKS, null);
		expect(reserves.length).toBeGreaterThan(0);
		expect(reserves.length).toBeLessThanOrEqual(4);
		const ids = communeKpis(BLOCKS).map((item) => item.id);
		const montres = reperesConnus(communeKpis(BLOCKS)).map((item) => item.id);
		for (const reserve of reserves) {
			expect(ids).toContain(reserve.kpi);
			if (montres.includes(reserve.kpi)) expect(reserve.ancre).toBe(`#kpi-${reserve.kpi}`);
			else expect(reserve.ancre).toMatch(/^#critere-(?:prix_commune|dpe)$/);
			expect(reserve.texte.trim()).not.toBe('');
		}
		expect(new Set(reserves.map((reserve) => reserve.texte)).size).toBe(reserves.length);
		expect(new Set(reserves.map((reserve) => reserve.kpi)).size).toBe(reserves.length);
	});

	it('never shows a technical value next to the synthesis (state key, enum, « unknown »)', () => {
		for (const reserve of reservesSynthese(BLOCKS, null)) {
			expect(reserve.texte).not.toMatch(/\b[a-z]+_[a-z_]+\b|\bunknown\b|^(?:État|Motif) source/);
		}
	});
});

// 04.7-15 T1 (Origami R8): each marker is « value + definition + base »; the definition is read on
// the card, not only in the Source panel.
describe('communeKpis — définition de chaque repère (04.7-15)', () => {
	it.each<[string, CommuneSynthese]>([['blocs datés', BLOCKS], ['colonnes plates', FLAT]])('%s : quatre définitions courtes, chacune distincte de son titre', (_cas, commune) => {
		const kpis = communeKpis(commune);
		for (const item of kpis) {
			expect(item.definition, item.id).toBeTruthy();
			expect(item.definition!.length, item.id).toBeLessThanOrEqual(120);
			expect(item.definition!.toLowerCase(), item.id).not.toBe(item.libelle.toLowerCase());
			expect(item.definition, item.id).toMatch(/\.$/);
		}
		expect(new Set(kpis.map((item) => item.definition)).size).toBe(4);
	});

	it('the flat DPE marker, titled « Classe DPE médiane », is defined as a median, the dated one as the most frequent class', () => {
		expect(kpi(FLAT, 'dpe-dominant').definition).toMatch(/médiane/i);
		expect(kpi(BLOCKS, 'dpe-dominant').definition).toMatch(/plus fréquente/i);
	});
});

// 04.7-15 T5 (Origami A4): no variation on a period that has begun. The current window of a trend
// that ends after the served end of the DVF delivery is not closed: the variation is not shown, and
// the reason is said.
describe('communeKpis — aucune variation sur une période entamée (04.7-15 A4)', () => {
	const livraison = (fin: string): IndicateursDvf['ancrage_temporel'] => ({
		etat: 'delivery_received_and_stock_bound', version_qualification: 'dvf-delivery-qualification.v3', lot_id: 'lot', periode_debut_inclus: '2021-01-01', periode_fin_exclue: fin,
		departements: ['85'], acquisition_complete: true, revisions_possibles: true, exhaustivite_historique: 'unknown', generation_fournisseur: null, date_observation: '2026-08-01',
		preuve_qualification_sha256: 'a', preuve_observation_sha256: 'b', preuve_reconstruction_sha256: 'c', snapshot_sha256: 'd', contenu_projete_sha256: 'e', cog_sha256: 'f',
		methode_projection: 'sha256-sorted-row-digests-json.v1', generation_stock: null
	});

	it('a current window that ends after the delivery: the served variation is masked, with its reason', () => {
		const block: IndicateursDvf = { ...DVF_BLOCK, ancrage_temporel: livraison('2025-07-01') };
		const [, appartements] = kpi({ ...BLOCKS, indicateurs_dvf: block }, 'tendance').lignes;
		expect(appartements.etat).toEqual({ kind: 'absence', etat: 'not_qualified', motif: PERIODE_ENTAMEE, effectif: 22, libelle: 'Période non close', aide: PERIODE_ENTAMEE });
		expect(PERIODE_ENTAMEE).toMatch(/entamée/);
		// The raw served value is not shown either.
		expect(appartements.brut.valeur).toBe('null');
	});

	it('a served absence on a begun period keeps its own state and reason', () => {
		const block: IndicateursDvf = { ...DVF_BLOCK, ancrage_temporel: livraison('2025-07-01') };
		const [maisons] = kpi({ ...BLOCKS, indicateurs_dvf: block }, 'tendance').lignes;
		expect(maisons.etat).toEqual({ kind: 'absence', etat: 'insufficient_sample', motif: 'Tendance non calculée.', effectif: 4 });
	});

	it('the theme row withholds the same variation', () => {
		const block: IndicateursDvf = { ...DVF_BLOCK, ancrage_temporel: livraison('2025-07-01') };
		const ligne = mapCommuneIndicators({ ...BLOCKS, indicateurs_dvf: block }).marche.find((row) => row.id === 'dvf-appartement-variation')!;
		expect(ligne.state).not.toBe('value');
		expect(ligne.value).toBe(PERIODE_ENTAMEE);
		const fermee = mapCommuneIndicators({ ...BLOCKS, indicateurs_dvf: { ...DVF_BLOCK, ancrage_temporel: livraison('2026-01-01') } }).marche.find((row) => row.id === 'dvf-appartement-variation')!;
		expect(fermee.value).toBe('3,2 %');
	});

	it('a closed window keeps the served variation', () => {
		const block: IndicateursDvf = { ...DVF_BLOCK, ancrage_temporel: livraison('2026-01-01') };
		const [, appartements] = kpi({ ...BLOCKS, indicateurs_dvf: block }, 'tendance').lignes;
		expect(appartements.etat).toEqual({ kind: 'valeur', texte: '+3,2', unite: '%' });
	});
});

describe('dates des repères en toutes lettres (04.7-15, F15)', () => {
	it('no month of a card is abbreviated', () => {
		const abrege = /\b(?:janv|févr|avr|juil|sept|oct|nov|déc)\./;
		for (const commune of [BLOCKS, FLAT, COMMUNE_85191]) {
			for (const item of communeKpis(commune, SOURCES_85191)) for (const texte of face(item)) expect(texte, item.id).not.toMatch(abrege);
		}
		expect(dateEnLettres('2026-09-26')).toBe('26 septembre 2026');
		expect(dateEnLettres('2021-07-01')).toBe('1er juillet 2021');
		expect(dateEnLettres('2021-07-11')).toBe('11 juillet 2021');
		expect(dateEnLettres('pas une date')).toBeNull();
	});
});

describe('fragmentsDeDates — a card line wraps between two days, never inside one', () => {
	it('keeps a first of the month (« 1er ») and a long month whole', () => {
		expect(fragmentsDeDates('du 1er septembre 2026 au 26 septembre 2026')).toEqual([
			{ texte: 'du ', date: false },
			{ texte: '1er septembre 2026', date: true },
			{ texte: ' au ', date: false },
			{ texte: '26 septembre 2026', date: true }
		]);
	});
});
