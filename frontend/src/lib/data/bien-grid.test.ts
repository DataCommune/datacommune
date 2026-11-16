import { describe, expect, it } from 'vitest';
import { mapBienToGrid } from './bien-grid';
import { mapBienToFiche } from './bien-fiche';
import { deriveDpeAttributionView, type DpeFicheSelection } from './dpe-attribution-view';
import { COPIE_DVF } from './dvf-attribution-view';
import { bienThemeCsvRows } from './bien-theme-csv';
import { neutralizeCsvValue } from '$lib/utils/csv-escape';
import { fusionnerProxies } from './bien-proxies';
import { mockResolve85, mockResolveAllEmpty } from './__fixtures__/resolve-85';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { COMMUNE_INDICATORS_85 } from './__fixtures__/commune-comparison-indicators';
import { GOLDEN_REAL_CORPUS } from './__fixtures__/golden-cases';
import baseline from './__fixtures__/fiche-grid-baseline.json';
import type { DatasetProvenance, DpePublicDiagnostic, DvfAttribution, DvfMutation } from '$lib/api/resolve';
import { mapBienCommuneContext } from './bien-commune-context';
import { mapBienHierarchy } from './bien-hierarchy';
import { withoutRepeatedHierarchyContext } from './bien-hierarchy-presentation';

// Reconstructed synthetic values, not live API observations or a dwelling attribution claim.
function mutation(id = 'synthetic-mutation'): DvfMutation {
	return { id_mutation: id, date_mutation: '2023-09-12', nature_mutation: 'Vente', valeur_fonciere: '150 000 – 200 000 €', type_local: 'Appartement', surface_reelle_bati: 78, nombre_pieces_principales: 3, prix_m2: '2 000 – 2 500 €/m²', id_parcelle: '85047000AB0042', classe_mutation: 'residentiel_appartement', prix_m2_raison: null, mutation_mono_local: true, surface_bati_totale: 78, hors_stats_vefa: false };
}
function selectedDpe(etiquette: string | null = 'F'): DpeFicheSelection {
	const diagnostic: DpePublicDiagnostic = { numero_dpe: 'DO-NOT-RENDER-DPE-ID', etage: null, batiment: null, porte: null, type_batiment: 'maison', surface_habitable: 78, etiquette_dpe: etiquette, etiquette_ges: 'D', annee_construction: 1972, periode_construction: '1948-1974', type_installation_chauffage: 'chaudière fioul', type_energie_principale_chauffage: 'Fioul domestique', conso_5_usages_ep: 412, emission_ges_5_usages: 98, cout_total_5_usages: 2310, qualite_isolation_enveloppe: 'insuffisante', date_etablissement_dpe: '2022-06-01', date_fin_validite_dpe: '2032-05-31', annee_diagnostic: 2022, perime: false };
	return { diagnostic, gapState: null };
}
const compatible: DvfAttribution = { status: 'compatible_parcelle', mutation: mutation() };
function priceCell(attribution: DvfAttribution | null) {
	const result = mapBienToGrid({ ...mockResolve85, dvf_attribution: attribution }, COMMUNE_85047_CHALLANS);
	return result.synthese.rows.find(row => row.concept.fieldId === 'bien.dvf.prix_m2')!.cells[result.column.id];
}

describe('Bien grid around existing attribution decisions', () => {
	it('preserves the frozen mapper values and every historical row', () => {
		const result = mapBienToGrid(mockResolve85, COMMUNE_85047_CHALLANS);
		expect(result.synthese.rows).toHaveLength(3);
		expect(result.fiche.themes.map(theme => theme.key)).toEqual(['marche', 'energie', 'risques', 'environnement', 'propriete']);
		baseline.synthetic.bien.kpi.forEach((expected, index) => expect(result.fiche.kpi[index]).toMatchObject(expected));
		baseline.synthetic.bien.themes.forEach((expected, index) => {
			for (const row of expected.rowsBien ?? []) expect(result.fiche.themes[index].rowsBien).toContainEqual(expect.objectContaining(row));
			for (const row of expected.rowsCommune ?? []) expect(result.fiche.themes[index].rowsCommune).toContainEqual(expect.objectContaining(row));
		});
	});
	it('does not elevate BAN, cadastral or address identity into a dwelling or building', () => {
		const result = mapBienToGrid({ ...mockResolve85, ban_id: 'BAN-only' }, null);
		expect(result.column).toMatchObject({ entity: 'adresse', scope: 'bien', grain: 'Adresse' });
		expect(result.fiche.kpi[2].value).toBe('85047000AB0042');
		expect(result.synthese.rows[2].cells[result.column.id].grain).toBe('Parcelle');
		const unknown = mapBienToGrid({ ...mockResolveAllEmpty, adresse: null }, null);
		expect(unknown.column).toMatchObject({ entity: 'adresse', grain: 'Point consulté', label: 'Adresse non disponible' });
	});
	it('preserves masked strings, parcel grain and exact public qualification', () => {
		expect(priceCell(compatible)).toMatchObject({ kind: 'value', display: '2 000 – 2 500 €/m²', grain: 'Parcelle', verdictLabel: 'Non attribuée', reserves: [COPIE_DVF.etats.compatible_parcelle.mention, COPIE_DVF.etats.compatible_parcelle.detail] });
	});
	it('qualifies cadastral value, source null, empty dataset and failure independently', () => {
		const value = mapBienToGrid({ ...mockResolve85, datasets: { cadastre: { id_parcelle: '0' } } }, null);
		expect(value.synthese.rows[2].cells[value.column.id]).toMatchObject({ kind: 'value', display: '0' });
		const missing = mapBienToGrid({ ...mockResolve85, datasets: { cadastre: { id_parcelle: null } } }, null);
		expect(missing.synthese.rows[2].cells[missing.column.id]).toMatchObject({ kind: 'absent', state: 'not_reported' });
		const empty = mapBienToGrid(mockResolveAllEmpty, null);
		expect(empty.synthese.rows[2].cells[empty.column.id]).toMatchObject({ kind: 'absent', state: 'out_of_coverage' });
		const failed = mapBienToGrid({ ...mockResolve85, erreurs: [{ dataset: 'cadastre', message: 'Synthetic outage' }] }, null);
		expect(failed.synthese.rows[2].cells[failed.column.id]).toMatchObject({ kind: 'absent', state: 'temporary_error' });
	});
	it.each([
		[{ status: 'absente', reason: 'aucune_mutation' }, 'out_of_coverage', COPIE_DVF.raisons.aucune_mutation],
		[{ status: 'non_attribuable', reason: 'vente_ensemble' }, 'out_of_coverage', COPIE_DVF.raisons.vente_ensemble],
		[{ status: 'non_attribuable', reason: 'vente_sur_plan' }, 'out_of_coverage', COPIE_DVF.raisons.vente_sur_plan],
		[{ status: 'error', reason: 'temporary_error' }, 'temporary_error', COPIE_DVF.raisons.temporary_error],
		[null, 'temporary_error', COPIE_DVF.raisons.temporary_error]
	] as const)('carries absence/technical decision %j without reading the first raw sale', (attribution, state, copy) => {
		const cell = priceCell(attribution);
		expect(cell).toMatchObject({ kind: 'absent', state, reserves: [copy.mention, copy.detail] });
		expect(cell).not.toHaveProperty('display');
		expect(cell).not.toHaveProperty('value');
	});
	it('keeps a missing field on an authorized mutation distinct from an absent transaction', () => {
		expect(priceCell({ status: 'compatible_parcelle', mutation: { ...mutation(), prix_m2: null } })).toMatchObject({ kind: 'absent', state: 'not_reported', reserves: [COPIE_DVF.etats.compatible_parcelle.mention, COPIE_DVF.etats.compatible_parcelle.detail] });
	});
	it('renders only the supplied DPE selection and preserves a null source field', () => {
		const selected = mapBienToGrid(mockResolve85, null, selectedDpe('B'));
		expect(selected.synthese.rows[0].cells[selected.column.id]).toMatchObject({ kind: 'value', display: 'B', dpeClass: 'B' });
		const missingField = mapBienToGrid(mockResolve85, null, selectedDpe(null));
		expect(missingField.synthese.rows[0].cells[missingField.column.id]).toMatchObject({ kind: 'absent', state: 'not_reported' });
		expect(JSON.stringify(selected)).not.toContain('DO-NOT-RENDER-DPE-ID');
	});
	it.each(['out_of_coverage', 'temporary_error'] as const)('retains DPE %s without borrowing the raw F diagnostic', gapState => {
		const result = mapBienToGrid(mockResolve85, null, { diagnostic: null, gapState });
		expect(result.synthese.rows[0].cells[result.column.id]).toMatchObject({ kind: 'absent', state: gapState });
		expect(result.themes.energie.bien.rows.every(row => row.cells[result.column.id].kind === 'absent')).toBe(true);
	});
	it('keeps the approved inventory-present carrier separate from attribution', () => {
		const carrier = GOLDEN_REAL_CORPUS.find(item => item.case_id === 'golden-address-001')!;
		if (carrier.type !== 'address') throw new Error('Expected approved address carrier');
		expect(carrier.invariants.dpe).toMatchObject({ dpe_inventory_presence: 'present', dpe_primary_diagnostic_present: false });
		const selection = deriveDpeAttributionView(carrier.resolve.dpe_attribution, { hrefBase: '/bien/test' }).ficheSelection;
		const result = mapBienToGrid(carrier.resolve, carrier.commune, selection);
		expect(result.synthese.rows[0].cells[result.column.id]).toMatchObject({ kind: 'absent', reserves: expect.arrayContaining(['Attribution au logement non établie']) });
	});
	it('retains multiple history rows sharing a field id with stable public source identities', () => {
		const base = mutation('first');
		const a = { ...mutation('history-a'), date_mutation: '2021-02-03', classe_mutation: 'vente_mixte' };
		const b = { ...mutation('history-b'), date_mutation: '2020-03-04', classe_mutation: 'vente_ensemble' };
		const envelope = { ...mockResolve85, datasets: { ...mockResolve85.datasets, dvf: [base, a, b] } };
		const result = mapBienToGrid(envelope, null);
		const history = result.themes.marche.bien.rows.filter(row => row.concept.label === COPIE_DVF.lignesBien.historique);
		expect(history).toHaveLength(2);
		expect(new Set(history.map(row => row.concept.id)).size).toBe(2);
		expect(history.map(row => row.cells[result.column.id])).toEqual(expect.arrayContaining([expect.objectContaining({ display: expect.stringContaining('03/02/2021'), grain: 'Parcelle' }), expect.objectContaining({ display: expect.stringContaining('04/03/2020'), grain: 'Parcelle' })]));
		const reversed = mapBienToGrid({ ...envelope, datasets: { ...envelope.datasets, dvf: [base, b, a] } }, null);
		expect(reversed.themes.marche.bien.rows.find(row => row.concept.id === history[0].concept.id)?.cells[result.column.id]).toEqual(history[0].cells[result.column.id]);
	});
	it('keeps independent commune proxy rows when synthesis is missing', () => {
		const result = mapBienToGrid(mockResolve85, null);
		expect(result.themes.risques.commune.rows.map(row => row.concept.fieldId)).toContain('bien.risques.inondation');
		expect(result.communeColumn.scope).toBe('commune');
	});
	it('keeps legacy history without source identifiers using distinct local positions', () => {
		const legacy = { date_mutation: '2021-02-03', classe_mutation: 'mixte' };
		const result = mapBienToGrid({ ...mockResolve85, datasets: { dvf: [mutation(), legacy, legacy] } }, null);
		const history = result.themes.marche.bien.rows.filter(row => row.concept.label === COPIE_DVF.lignesBien.historique);
		expect(history).toHaveLength(2);
		expect(history[0].concept.id).not.toBe(history[1].concept.id);
		expect(history[0].cells[result.column.id]).toEqual(history[1].cells[result.column.id]);
	});
	it('does not borrow resolve vintages for communal figures or lose public reservations', () => {
		const provenance: DatasetProvenance = { dataset: 'revenus', nom: 'Source revenus synthétique', url: 'https://example.org/revenus', licence: 'Test', producteur: 'Test', periode: { statut: 'connue', label: '2026', debut: null, fin: null }, derniere_importation_reussie: null, couverture: { type: 'inconnue', departements: null }, cadence: { statut: 'non_publiee_ou_irreguliere', label: null, evidence_url: null, intervalle_jours: null }, etat: 'inconnu' };
		const result = mapBienToGrid(mockResolve85, { ...COMMUNE_85047_CHALLANS, annee_revenus: null }, undefined, { provenances: { revenus: provenance } });
		const income = result.themes.environnement.commune.rows.find(row => row.concept.fieldId === 'revenus.revenu_median')!.cells[result.communeColumn.id];
		expect(income).toMatchObject({ millesime: null, source: { nom: provenance.nom }, reserves: [expect.stringContaining('avant redistribution')] });
		const vacancy = result.themes.propriete.commune.rows.find(row => row.concept.fieldId === 'vacance.taux_vacance')!.cells[result.communeColumn.id];
		expect(vacancy).toMatchObject({ grain: 'Parc privé communal', reserves: [expect.stringContaining('toutes durées')] });
	});
	it('qualifies additive Commune theme cells identically to the hierarchy before exact deduplication', () => {
		const result = mapBienToGrid(mockResolve85, COMMUNE_INDICATORS_85);
		const communal = mapBienCommuneContext(COMMUNE_INDICATORS_85)!;
		const hierarchy = mapBienHierarchy(mockResolve85, null, {
			version: 2, status: 'unassigned', reason: 'dwelling_identity_unproven', diagnostic: null,
			candidates: [], building_context: null, graph_state: 'not_checked'
		}, communal);
		const theme = result.themes.marche.commune;
		const suffix = 'dvf-maison-mediane-courante';
		const themeRow = theme.rows.find((row) => row.concept.id.endsWith(suffix))!;
		const hierarchyRow = hierarchy.grid.rows.find((row) => row.concept.id.endsWith(suffix))!;
		const themeCell = themeRow.cells[result.communeColumn.id];
		const hierarchyCell = hierarchyRow.cells.commune;

		expect(themeCell).toEqual(hierarchyCell);
		expect(themeCell).toMatchObject({
			kind: 'value', state: 'value', display: '3\u202f100 €/m²',
			source: { nom: 'Source DVF 85', url: 'reference-dvf 85' },
			millesime: '2025-04-01 → 2026-04-01 (fin exclue)',
			criticalSummary: expect.any(Object)
		});
		expect(withoutRepeatedHierarchyContext(theme, hierarchy).rows).not.toContain(themeRow);

		const changedCell = { ...themeCell, reserves: [...themeCell.reserves, 'Qualification différente.'] };
		const changedTheme = { ...theme, rows: theme.rows.map((row) => row === themeRow
			? { ...row, cells: { ...row.cells, [result.communeColumn.id]: changedCell } }
			: row) };
		expect(withoutRepeatedHierarchyContext(changedTheme, hierarchy).rows)
			.toContain(changedTheme.rows.find((row) => row.concept.id.endsWith(suffix)));
		expect(themeCell.reserves).toEqual(hierarchyCell.reserves);
	});
	it('keeps exports values, parcel grain and attribution reservations; formula guard remains applicable', () => {
		const result = mapBienToGrid({ ...mockResolve85, dvf_attribution: compatible }, COMMUNE_85047_CHALLANS);
		const theme = result.fiche.themes.find(theme => theme.key === 'marche')!;
		const rows = bienThemeCsvRows(theme);
		// Compare historical values; qualified grid grains may refine the Commune fallback.
		const old = bienThemeCsvRows(mapBienToFiche(mockResolve85, COMMUNE_85047_CHALLANS).themes[0])
			.filter(([label]) => !label.startsWith('Portée — ') && !label.startsWith('Réserve — '));
		for (const row of old) expect(rows).toContainEqual(row);
		expect(rows.some(row => row[1] === 'Parcelle')).toBe(true);
		expect(rows.some(row => row[1].includes(COPIE_DVF.etats.compatible_parcelle.detail))).toBe(true);
		const attack = '=HYPERLINK("https://example.org")';
		const hostile = bienThemeCsvRows({ ...theme, rowsBien: [{ label: 'Synthetic', value: '0', state: 'value', presentation: { state: 'value', reserves: [attack] } }], rowsCommune: [] });
		expect(hostile).toContainEqual(['Réserve — Synthetic', attack]);
		expect(neutralizeCsvValue(hostile[1][1])).toBe(`'${attack}`);
	});
	it('preserves pending-to-failed proxy degradation and its available Commune figures', () => {
		const fast = { ...mockResolve85, datasets: { cadastre: mockResolve85.datasets!.cadastre } };
		const pending = mapBienToGrid(fast, COMMUNE_85047_CHALLANS);
		const merged = fusionnerProxies(fast, { datasets: {}, erreurs: [{ dataset: 'urbanisme', message: 'Synthetic outage' }], avertissements: [], dataset_states: null, provenance: [] });
		const failed = mapBienToGrid(merged, COMMUNE_85047_CHALLANS);
		expect(pending.themes.environnement.bien.rows[0].cells[pending.column.id]).toMatchObject({ kind: 'absent', state: 'out_of_coverage' });
		expect(failed.themes.environnement.bien.rows[0].cells[failed.column.id]).toMatchObject({ kind: 'absent', state: 'temporary_error' });
		expect(failed.themes.environnement.commune).toEqual(pending.themes.environnement.commune);
	});
});
