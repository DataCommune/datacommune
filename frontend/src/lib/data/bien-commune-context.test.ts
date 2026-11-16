import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, IndicateursDpe, IndicateursDvf, ProvenanceDvf } from '$lib/api/commune';
import { mapBienCommuneContext } from './bien-commune-context';

const dvfSource: ProvenanceDvf = {
	nom: 'DVF reçu', producteur: 'DGFiP', licence: 'LO', reference: 'lot-dvf',
	date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown'
};
const dpeSource = {
	nom: 'DPE reçu', producteur: 'ADEME', licence: 'Etalab', reference: 'stock-dpe',
	date_publication_source: null, date_snapshot: '2026-08-04', date_import: '2026-08-05', couverture: 'partial' as const
};
const value = (valeur: number) => ({ etat: 'value' as const, valeur });
const unavailable = (motif: string) => ({ etat: 'insufficient_sample' as const, motif });
const stats = (p50: number, effectif: number, debut = '2025-01-01', fin = '2026-01-01') => ({
	fenetre: { debut_inclus: debut, fin_exclue: fin }, effectif,
	mediane: value(p50),
	quantiles: { etat: 'value' as const, p10: p50 - 500, p50, p90: p50 + 700, qualification: 'published' }
});

const dvf: IndicateursDvf = {
	version_contrat: '1.1', version_population: 'dvf-pop-v2', date_calcul: '2026-08-03', generation: 'dvf-g1',
	provenance: [dvfSource], ancrage_temporel: { etat: 'unknown', motif: 'Révisions rétrospectives possibles.' },
	population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune', unite_prix: 'EUR/m2',
	methode_quantiles: 'percentile_cont', methode_variation: '100*(mediane_courante/mediane_precedente-1)',
	residentiel_maison: {
		precedente: stats(2_200, 18, '2024-01-01', '2025-01-01'), courante: stats(2_400, 21), variation_pct: value(9.1),
		qualification_tendance: 'Fenêtres comparables.'
	},
	residentiel_appartement: {
		precedente: stats(3_100, 9, '2024-01-01', '2025-01-01'),
		courante: { ...stats(3_200, 3), mediane: unavailable('Effectif sous le seuil publié.'), quantiles: unavailable('Effectif sous le seuil publié.') },
		variation_pct: unavailable('Tendance non publiable.'), qualification_tendance: null
	}
};

const dpe: IndicateursDpe = {
	version_contrat: '1.0', version_population: 'dpe-pop-v1', date_calcul: '2026-08-06', generation: 'dpe-g1',
	provenance: [dpeSource], population: 'diagnostics_canoniques_disponibles_hors_traces_retirees',
	periode: 'bornes_etablissement_observees_non_exhaustives', date_evaluation: '2026-08-04',
	methode_mediane: 'percentile_disc', methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G',
	resultat: {
		etat: 'value',
		population: {
			total: 12, classes_connues: 10,
			types_batiment: { maison: 5, appartement: 4, autre: 1, inconnu: 2 },
			validite: { unknown: 1, expired: 2, not_expired_by_date: 8, inconsistent_dates: 1, future_establishment: 0 },
			remplacement: { not_checked: 12, no_successor_known: 0, replaced: 0, ambiguous: 0 },
			regimes: { existant: 9, neuf: 2, inconnu: 1 }, source_inconnue: 2, revision_non_verifiee: 12
		},
		distribution: { classes: { A: 1, B: 1, C: 3, D: 2, E: 3, F: 0, G: 0 }, inconnue: 2 },
		modes: ['C', 'E'], mediane_ordinale: 'D', part_fg_pct: value(0),
		date_etablissement_min: '2020-01-01', date_etablissement_max: '2026-07-31', dates_etablissement_inconnues: 2
	}
};

const commune = { code_insee: '85047', indicateurs_dvf: dvf, indicateurs_dpe: dpe } as CommuneSynthese;

describe('mapBienCommuneContext', () => {
	it('échoue fermé sans commune ou sans blocs HTTP additifs qualifiés', () => {
		expect(mapBienCommuneContext(null)).toBeNull();
		expect(mapBienCommuneContext({ ...commune, indicateurs_dvf: undefined, indicateurs_dpe: undefined })).toBeNull();
	});

	it('réemploie toutes les lignes propriétaires avec leur valeur, grain, fenêtre et source', () => {
		const grid = mapBienCommuneContext(commune)!;
		expect(grid.columns).toEqual([
			{ id: 'commune', entity: 'commune', scope: 'commune', grain: 'Commune', label: 'Commune', code: '85047' }
		]);
		expect(grid.rows.map((row) => row.concept.id)).toEqual([
			'bien-commune-context:dvf-maison-mediane-courante', 'bien-commune-context:dvf-maison-effectif-courant',
			'bien-commune-context:dvf-maison-quantiles-courants', 'bien-commune-context:dvf-maison-mediane-precedente',
			'bien-commune-context:dvf-maison-effectif-precedent', 'bien-commune-context:dvf-maison-variation',
			'bien-commune-context:dvf-appartement-mediane-courante', 'bien-commune-context:dvf-appartement-effectif-courant',
			'bien-commune-context:dvf-appartement-quantiles-courants', 'bien-commune-context:dvf-appartement-mediane-precedente',
			'bien-commune-context:dvf-appartement-effectif-precedent', 'bien-commune-context:dvf-appartement-variation',
			'bien-commune-context:context:dvf-ancrage', 'bien-commune-context:context:dpe-population',
			'bien-commune-context:context:dpe-distribution',
			'bien-commune-context:context:dpe-periode', 'bien-commune-context:dpe-mediane',
			'bien-commune-context:dpe-modes', 'bien-commune-context:dpe-part-fg'
		]);
		const house = grid.rows[0].cells.commune;
		expect(house).toMatchObject({
			kind: 'value', display: '2\u202f400 €/m²', grain: 'Commune',
			millesime: '2025-01-01 → 2026-01-01 (fin exclue)',
			source: { nom: 'DVF reçu', url: 'lot-dvf' }
		});
		expect(house.reserves.join(' ')).toContain('2025-01-01');
		expect(house.reserves.join(' ')).toContain('DVF reçu');
		const dpePopulation = grid.rows.find((row) => row.concept.id.endsWith('dpe-population'))!.cells.commune;
		expect(dpePopulation).toMatchObject({
			source: { nom: 'DPE reçu', url: 'stock-dpe' },
			millesime: 'Évaluation : 2026-08-04'
		});
		expect(dpePopulation.reserves.join(' ')).toContain('DPE reçu');
		expect(dpePopulation.reserves.join(' ')).toContain('dpe-pop-v1');
		const dpeBounds = grid.rows.find((row) => row.concept.id.endsWith('dpe-periode'))!.cells.commune;
		expect(dpeBounds).toMatchObject({
			kind: 'context', text: 'du 1er janvier 2020 au 31 juillet 2026',
			millesime: 'Évaluation : 2026-08-04'
		});
		expect(dpeBounds.reserves).toContain('Évaluation\u00a0: 2026-08-04');
		expect(dpeBounds.reserves).toContain('Dates d’établissement inconnues : 2');
	});

	it('ne substitue aucune métadonnée historique aux DTO qualifiés', () => {
		const grid = mapBienCommuneContext(commune)!;
		const cells = grid.rows.map((row) => row.cells.commune);
		expect(cells.filter((cell) => cell.source?.nom === 'DVF reçu')).toHaveLength(13);
		expect(cells.filter((cell) => cell.source?.nom === 'DPE reçu')).toHaveLength(6);
		expect(cells.every((cell) => cell.source?.nom !== 'Demandes de Valeurs Foncières (DVF)')).toBe(true);
		expect(cells.every((cell) => cell.millesime !== '2023')).toBe(true);
		expect(grid.rows.find((row) => row.concept.id.endsWith('dvf-maison-mediane-precedente'))!.cells.commune.millesime)
			.toBe('2024-01-01 → 2025-01-01 (fin exclue)');
	});

	it('conserve les zéros, modes multiples et absences sans recalcul client', () => {
		const grid = mapBienCommuneContext(commune)!;
		expect(grid.rows.find((row) => row.concept.id.endsWith('dvf-appartement-mediane-courante'))!.cells.commune)
			.toMatchObject({ kind: 'absent', state: 'not_reported', reason: expect.objectContaining({ code: 'insufficient_sample' }) });
		expect(grid.rows.find((row) => row.concept.id.endsWith('dvf-appartement-effectif-courant'))!.cells.commune)
			.toMatchObject({ kind: 'value', display: '3' });
		expect(grid.rows.find((row) => row.concept.id.endsWith('dpe-modes'))!.cells.commune)
			.toMatchObject({ kind: 'value', display: 'C, E' });
		expect(grid.rows.find((row) => row.concept.id.endsWith('dpe-part-fg'))!.cells.commune)
			.toMatchObject({ kind: 'value', display: '0 %', measuredZero: true });
	});

	it('rend une absence contractuelle explicite sans ancien agrégat de substitution', () => {
		const grid = mapBienCommuneContext({
			...commune,
			indicateurs_dvf: { etat: 'not_qualified', motif: 'Version HTTP non qualifiée.' },
			indicateurs_dpe: { etat: 'suppressed', motif: 'Résultat non publié.' },
			prix_m2_median: 9_999,
			dpe_median: 'A'
		})!;
		expect(grid.rows).toHaveLength(2);
		expect(grid.rows.map((row) => row.cells.commune)).toEqual([
			expect.objectContaining({
				kind: 'absent', state: 'not_reported', grain: 'Commune',
				reason: expect.objectContaining({ label: 'Version HTTP non qualifiée.', code: 'source_reason' }),
				reserves: expect.arrayContaining(['État source : not_qualified', 'Motif : Version HTTP non qualifiée.'])
			}),
			expect.objectContaining({
				kind: 'absent', state: 'not_reported', grain: 'Commune',
				reason: expect.objectContaining({ label: 'Résultat non publié.', code: 'suppressed' }),
				reserves: expect.arrayContaining(['État source : suppressed', 'Motif : Résultat non publié.'])
			})
		]);
		expect(JSON.stringify(grid)).not.toContain('9999');
		expect(grid.rows.some((row) => {
			const cell = row.cells.commune;
			return cell.kind === 'value' && cell.display === 'A';
		})).toBe(false);
	});
});
