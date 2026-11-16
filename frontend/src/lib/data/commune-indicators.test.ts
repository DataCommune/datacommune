import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, IndicateursDpe, IndicateursDvf, LivraisonDvfRecue } from '$lib/api/commune';
import { mapCommuneIndicators } from './commune-indicators';

const source = { nom: 'Source', producteur: 'Producteur', licence: 'LO', reference: 'ref', date_publication_source: null, date_snapshot: '2026-08-01', date_import: '2026-08-02', couverture: 'unknown' as const };
const number = (valeur: number) => ({ etat: 'value' as const, valeur });
const absent = (motif: string) => ({ etat: 'insufficient_sample' as const, motif });
const stats = (p50: number, effectif: number, p10 = p50 - 100, p90 = p50 + 100) => ({
	fenetre: { debut_inclus: '2025-01-01', fin_exclue: '2026-01-01' }, effectif,
	mediane: number(p50), quantiles: { etat: 'value' as const, p10, p50, p90, qualification: 'qualified' }
});
const dvf: IndicateursDvf = {
	version_contrat: '1.1', version_population: 'v1', date_calcul: '2026-08-03', generation: 'g1', provenance: [source],
	ancrage_temporel: { etat: 'unknown', motif: 'Révisions rétrospectives possibles.' },
	population: 'mutations_residentielles_mono_local_hors_vefa_mono_commune', unite_prix: 'EUR/m2',
	methode_quantiles: 'percentile_cont', methode_variation: '100*(mediane_courante/mediane_precedente-1)',
	residentiel_maison: { precedente: stats(2000, 10), courante: stats(2000, 12, 2000, 2000), variation_pct: number(0), qualification_tendance: 'qualified' },
	residentiel_appartement: { precedente: stats(3000, 8), courante: { ...stats(3100, 0), mediane: absent('Effectif insuffisant'), quantiles: absent('Effectif insuffisant') }, variation_pct: absent('Tendance indisponible'), qualification_tendance: null }
};
dvf.residentiel_maison.precedente.fenetre = { debut_inclus: '2024-01-01', fin_exclue: '2025-01-01' };
dvf.residentiel_appartement.precedente.fenetre = { debut_inclus: '2024-01-01', fin_exclue: '2025-01-01' };
const dpe: IndicateursDpe = {
	version_contrat: '1.0', version_population: 'v1', date_calcul: '2026-08-03', generation: 'g1', provenance: [source],
	population: 'diagnostics_canoniques_disponibles_hors_traces_retirees', periode: 'bornes_etablissement_observees_non_exhaustives', date_evaluation: '2026-08-01', methode_mediane: 'percentile_disc', methode_modes: 'toutes_classes_frequence_maximale_ordre_A_G',
	resultat: { etat: 'value', population: { total: 6, classes_connues: 5, types_batiment: { maison: 2, appartement: 2, autre: 1, inconnu: 1 }, validite: { unknown: 1, expired: 1, not_expired_by_date: 4, inconsistent_dates: 0, future_establishment: 0 }, remplacement: { not_checked: 5, no_successor_known: 1, replaced: 0, ambiguous: 0 }, regimes: { existant: 4, neuf: 1, inconnu: 1 }, source_inconnue: 0, revision_non_verifiee: 5 }, distribution: { classes: { A: 0, B: 0, C: 2, D: 1, E: 2, F: 0, G: 0 }, inconnue: 1 }, modes: ['C', 'E'], mediane_ordinale: 'D', part_fg_pct: number(0), date_etablissement_min: '2020-01-01', date_etablissement_max: '2026-07-31', dates_etablissement_inconnues: 1 }
};
const base = { code_insee: '85047', indicateurs_dvf: dvf, indicateurs_dpe: dpe } as CommuneSynthese;

describe('mapCommuneIndicators', () => {
	it('préserve variation zéro, effectif zéro, quantiles égaux et distingue modes de médiane', () => {
		const result = mapCommuneIndicators(base);
		expect(result.marche.find((r) => r.id === 'dvf-maison-variation')?.value).toBe('0 %');
		expect(result.marche.find((r) => r.id === 'dvf-appartement-effectif-courant')?.value).toBe('0');
		expect(result.marche.find((r) => r.id === 'dvf-maison-quantiles-courants')?.value).toMatch(/^2\D000 €\/m² \(P10 = P90\)$/);
		expect(result.energie.find((r) => r.id === 'dpe-modes')?.value).toBe('C, E');
		expect(result.energie.find((r) => r.id === 'dpe-mediane')?.value).toBe('D');
	});

	it('ne fabrique aucun chiffre pour un ancien payload ou un bloc absent', () => {
		expect(mapCommuneIndicators({ ...base, indicateurs_dvf: undefined, indicateurs_dpe: undefined }).marche).toEqual([]);
		const result = mapCommuneIndicators({ ...base, indicateurs_dvf: { etat: 'not_qualified', motif: 'Build non qualifié' } });
		expect(result.marche).toHaveLength(1);
		expect(result.marche[0]).toMatchObject({ state: 'not_reported', available: false, value: 'Build non qualifié' });
		expect(result.marche[0].presentation).toMatchObject({ reason: { label: 'Build non qualifié' } });
	});

	it('words a served reason by the glossary table and never shows a technical key (03-17 F57, F58)', () => {
		const table = mapCommuneIndicators({ ...base, indicateurs_dvf: { etat: 'not_qualified', motif: 'indicator_policy_unqualified' } });
		expect(table.marche[0]).toMatchObject({ value: 'Indicateur non encore validé' });
		expect(table.marche[0].presentation?.reserves).toContain('Motif : Indicateur non encore validé');
		const cle = mapCommuneIndicators({ ...base, indicateurs_dvf: { etat: 'not_qualified', motif: 'some_unknown_key' } });
		expect(cle.marche[0]).toMatchObject({ value: 'Non qualifié' });
		expect(JSON.stringify(cle.marche[0])).not.toContain('some_unknown_key');
	});

	it('place périodes, populations, provenance, grain et réserves dans les cellules', () => {
		const result = mapCommuneIndicators(base);
		const price = result.marche.find((r) => r.id === 'dvf-maison-mediane-courante')!;
		expect(price.presentation?.grain).toBe('Commune');
		expect(price.presentation?.reserves?.join(' ')).toContain('2025-01-01');
		expect(price.presentation?.reserves?.join(' ')).toContain('12 mutations');
		expect(price.presentation?.reserves?.join(' ')).toContain('Source');
		expect(price.presentation?.reserves?.join(' ')).toContain('Producteur');
		expect(price.presentation?.reserves?.join(' ')).toContain('Licence');
		expect(price.presentation?.reserves?.join(' ')).toContain('Référence');
		expect(price.presentation?.reserves?.join(' ')).toContain('Snapshot');
		expect(price.presentation?.reserves?.join(' ')).toContain('Couverture');
		expect(price.presentation?.reserves?.join(' ')).toContain('Génération');
		expect(result.energie.find((r) => r.id === 'dpe-population')?.value).toBe('6\u00a0diagnostics disponibles');
	});

	it('conserve l’état et le motif source sur chaque absence numérique', () => {
		const result = mapCommuneIndicators(base);
		const median = result.marche.find((r) => r.id === 'dvf-appartement-mediane-courante')!;
		expect(median.presentation?.reserves).toEqual(expect.arrayContaining(['État source : insufficient_sample', 'Motif : Effectif insuffisant']));
	});

	it.each([
		['not_qualified', 'indicator_policy_unqualified', 'not_reported', 'Indicateur non encore validé'],
		['not_qualified', 'territorial_attribution_unqualified', 'not_reported', 'Rattachement à la commune non vérifié'],
		['not_reported', 'empty_sample', 'not_reported', 'Aucune vente retenue sur la période'],
		['zero_denominator', 'known_class_denominator_zero', 'not_applicable', 'Aucun diagnostic avec une classe connue']
	] as const)('traduit %s/%s par le glossaire, jamais par sa clé (03-17 F58)', (etat, motif, state, label) => {
		const result = mapCommuneIndicators({ ...base, indicateurs_dvf: { etat, motif } });
		expect(result.marche[0]).toMatchObject({ state, value: label });
		expect(result.marche[0].presentation?.reserves).toContain(`État source : ${etat}`);
		expect(result.marche[0].presentation?.reserves).toContain(`Motif : ${label}`);
		expect(JSON.stringify(result.marche[0])).not.toContain(motif);
	});

	it('un motif hors table et technique n’est jamais affiché : l’état servi le remplace (03-17 F57)', () => {
		const result = mapCommuneIndicators({ ...base, indicateurs_dvf: { etat: 'out_of_coverage', motif: 'code_backend_inconnu' } });
		expect(result.marche[0]).toMatchObject({ state: 'out_of_coverage', value: 'Hors couverture' });
		expect(result.marche[0].presentation?.reserves).toContain('État source : out_of_coverage');
		expect(JSON.stringify(result.marche[0])).not.toContain('code_backend_inconnu');
	});

	it('explicite la population DVF et les deux fenêtres avec leurs effectifs sur la variation', () => {
		const variation = mapCommuneIndicators(base).marche.find((r) => r.id === 'dvf-maison-variation')!;
		const reserves = variation.presentation?.reserves?.join(' ') ?? '';
		expect(reserves).toContain('mutations résidentielles mono-local hors ventes sur plan mono-commune');
		expect(reserves).toContain('12 mutations');
		expect(reserves).toContain('10 mutations');
		expect(reserves).toContain('2024-01-01');
		expect(reserves).toContain('2025-01-01');
	});

	it('ne transforme pas les inconnus structurés en valeurs textuelles', () => {
		const changedDvf: IndicateursDvf = structuredClone(dvf);
		const changedDpe: IndicateursDpe = structuredClone(dpe);
		changedDvf.residentiel_maison.courante.effectif = null;
		if (changedDpe.resultat.etat !== 'value') throw new Error('fixture DPE invalide');
		changedDpe.resultat.mediane_ordinale = null;
		changedDpe.resultat.modes = [];
		const result = mapCommuneIndicators({ ...base, indicateurs_dvf: changedDvf, indicateurs_dpe: changedDpe });
		expect(result.marche.find((r) => r.id === 'dvf-maison-effectif-courant')).toMatchObject({ available: false, state: 'not_reported' });
		expect(result.energie.find((r) => r.id === 'dpe-mediane')).toMatchObject({ available: false, state: 'not_reported' });
		expect(result.energie.find((r) => r.id === 'dpe-modes')).toMatchObject({ available: false, state: 'not_reported' });
	});

	it('conserve les dimensions de population DPE sans assimiler diagnostics et logements', () => {
		const population = mapCommuneIndicators(base).energie.find((r) => r.id === 'dpe-population')!;
		const reserves = population.presentation?.reserves?.join(' ') ?? '';
		for (const expected of ['6\u00a0diagnostics disponibles', '5 classes connues', '1 classe inconnue', 'Validité expired : 1', 'Remplacement not_checked : 5', 'Révisions non vérifiées : 5']) expect(reserves).toContain(expected);
		expect(reserves).toContain('Ces diagnostics ne représentent pas un décompte de logements.');
	});

	it('conserve le lot et les réserves d’un ancrage DVF reçu', () => {
		const changedDvf: IndicateursDvf = structuredClone(dvf);
		changedDvf.ancrage_temporel = {
			etat: 'delivery_received_and_stock_bound', version_qualification: 'dvf-delivery-qualification.v2', lot_id: 'lot-2026', periode_debut_inclus: '2020-01-01', periode_fin_exclue: '2026-01-01', departements: ['44', '85'], acquisition_complete: true, revisions_possibles: true, exhaustivite_historique: 'unknown', generation_fournisseur: null, date_observation: '2026-08-03', preuve_qualification_sha256: 'a', preuve_observation_sha256: 'b', preuve_reconstruction_sha256: 'c', snapshot_sha256: 'd', contenu_projete_sha256: 'e', cog_sha256: 'f', methode_projection: 'sha256-sorted-row-digests-json.v1', generation_stock: null
		};
		const anchor = mapCommuneIndicators({ ...base, indicateurs_dvf: changedDvf }).marche.find((r) => r.id === 'dvf-ancrage')!;
		expect(anchor.value).toContain('lot-2026');
		expect(anchor.value).toContain('révisions possibles');
		expect(anchor.value).toContain('exhaustivité historique inconnue');
		expect(anchor.value).toContain('2020-01-01');
	});

	// 04.1-10 report: a national lot is qualified under v3 with its received departements; it reads as v2.
	it('lit un lot qualifié en v3 sur ses départements reçus comme un lot v2', () => {
		const ancrage = (version: LivraisonDvfRecue['version_qualification'], departements: string[]): IndicateursDvf => {
			const changedDvf: IndicateursDvf = structuredClone(dvf);
			changedDvf.ancrage_temporel = {
				etat: 'delivery_received_and_stock_bound', version_qualification: version, lot_id: 'lot-2026', periode_debut_inclus: '2020-01-01', periode_fin_exclue: '2026-01-01', departements, acquisition_complete: true, revisions_possibles: true, exhaustivite_historique: 'unknown', generation_fournisseur: null, date_observation: '2026-08-03', preuve_qualification_sha256: 'a', preuve_observation_sha256: 'b', preuve_reconstruction_sha256: 'c', snapshot_sha256: 'd', contenu_projete_sha256: 'e', cog_sha256: 'f', methode_projection: 'sha256-sorted-row-digests-json.v1', generation_stock: null
			};
			return changedDvf;
		};
		const v2 = mapCommuneIndicators({ ...base, indicateurs_dvf: ancrage('dvf-delivery-qualification.v2', ['44', '85']) });
		const v3 = mapCommuneIndicators({ ...base, indicateurs_dvf: ancrage('dvf-delivery-qualification.v3', ['44', '75', '85']) });
		expect(v3).toEqual(v2);
	});
});

// 04.7-15 (F15): the bounds of the DPE establishment dates are spelled in full, never raw ISO.
describe('mapCommuneIndicators — bornes des DPE en toutes lettres (04.7-15)', () => {
	it('both bounds served: « du … au … »; one unknown: said as unknown', () => {
		expect(mapCommuneIndicators(base).energie.find((r) => r.id === 'dpe-periode')?.value).toBe('du 1er janvier 2020 au 31 juillet 2026');
		const sansDebut = { ...dpe, resultat: { ...dpe.resultat, date_etablissement_min: null } } as IndicateursDpe;
		expect(mapCommuneIndicators({ ...base, indicateurs_dpe: sansDebut }).energie.find((r) => r.id === 'dpe-periode')?.value).toBe('début inconnu → 31 juillet 2026');
	});
});
