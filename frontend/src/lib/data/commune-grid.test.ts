import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import { ouvrirVolet, panneauDeTheme } from './__fixtures__/fiche-commune-rendue';
import { downloadCsv } from '$lib/utils/csv-download';
import { COMMUNE_THEMES, THEME_DU_CHAMP, mapCommuneToGrid, themesCommune, themesPourExport } from './commune-grid';
import { mapCommuneToFiche, ficheDepth } from './commune-fiche';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import baseline from './__fixtures__/fiche-grid-baseline.json';
import rentFixture from './__fixtures__/loyers-cor06-api.json';
import { COMMUNE_INDICATORS_44 } from './__fixtures__/commune-comparison-indicators';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './__fixtures__/commune-85191-20261012';
import { getFieldContract } from './formatters';
import { COMMUNE_META_SOURCE_NOMS, COMMUNE_META_SOURCE_ORDER } from './explications';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('Commune grid from existing source fields', () => {
	it('prioritizes qualified current prices, populations, trends and distinct DPE summaries', () => {
		const result = mapCommuneToGrid(COMMUNE_INDICATORS_44);
		const ids = result.synthese.rows.map((row) => row.concept.fieldId ?? row.concept.id);
		expect(ids).toEqual([
			'dvf.indicateurs.maison.mediane_courante',
			'commune-synthese:dvf-maison-effectif-courant',
			'dvf.indicateurs.appartement.mediane_courante',
			'commune-synthese:dvf-appartement-effectif-courant',
			'dvf.indicateurs.maison.variation_pct',
			'dvf.indicateurs.appartement.variation_pct',
			'commune-synthese:dpe-mediane',
			'commune-synthese:dpe-modes'
		]);
		const cells = result.synthese.rows.map((row) => row.cells[result.column.id]);
		expect(cells[0].reserves.join(' ')).toContain('Période :');
		expect(cells[0].reserves.join(' ')).toMatch(/\d+ mutations/);
		expect(cells[6]).toMatchObject({ kind: 'value', display: 'D' });
		expect(cells[7]).toMatchObject({ kind: 'value', display: 'C, E' });
		expect(result.synthese.rows[7].concept.label).toBe('Classes les plus fréquentes, ex aequo');

		const themeRows = Object.values(result.themes).flatMap((grid) => grid.rows);
		for (const summary of result.synthese.rows) {
			const same = themeRows.find((row) => row.concept.fieldId === summary.concept.fieldId && row.concept.label === summary.concept.label);
			const themeCell = same?.cells[result.column.id];
			expect(themeCell).toBeDefined();
			expect(themeCell).toEqual(summary.cells[result.column.id]);
		}
	});
	it('builds compact rent boundaries from typed numbers, including zero and missing uncertainty', () => {
		const source = rentFixture.communes['05066'].donnees[0] as CommuneSynthese;
		const commune = { ...source, loyers_appartement: { ...source.loyers_appartement!, nb_observations_commune: 0,
			loyer_m2_bas: null, loyer_m2_haut: null, type_prediction: 'epci', surface_reference_m2: 52.123456 } };
		const result = mapCommuneToGrid(commune);
		const rent = result.synthese.rows[0].cells[result.column.id];
		expect(rent.grain).toBe('EPCI');
		expect(rent.criticalSummary?.facts.join(' ')).toContain('52,123456 m²');
		expect(rent.criticalSummary?.warnings).toContain('0 observations communales');
		expect(rent.criticalSummary?.warnings).toContain('incertitude non renseignée');
		expect(rent.reserves.join(' ')).toContain('Aucune évolution inter-cartes déduite');
	});
	it('never borrows rental detail or an income year when the typed fields are absent', () => {
		const result = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, annee_revenus: null, loyers_appartement: null });
		const rent = result.synthese.rows[0].cells[result.column.id];
		expect(rent.grain).toBe('Maille d’estimation inconnue');
		expect(rent.criticalSummary?.warnings.join(' ')).toContain('non renseignés');
		expect(rent.criticalSummary?.facts.join(' ')).not.toContain('2025');
		expect(result.synthese.rows[3].cells[result.column.id].criticalSummary?.facts).toEqual(['Avant redistribution']);
	});
	it('keeps source dates local to each dataset and never dates DVF from another source', () => {
		const sources = COMMUNE_META_SOURCE_ORDER.map(dataset => ({ nom: COMMUNE_META_SOURCE_NOMS[dataset], url: `https://example.org/${dataset}` }));
		const result = mapCommuneToGrid(COMMUNE_85047_CHALLANS, sources);
		expect(result.column).toEqual({ id: 'commune:85047', entity: 'commune', scope: 'commune', grain: 'Commune', label: 'Challans', code: '85047' });
		expect(result.synthese.rows[3].cells[result.column.id]).toMatchObject({ millesime: '2021', source: { nom: 'Filosofi (revenus et pauvreté)' } });
		expect(result.synthese.rows[2].cells[result.column.id].millesime).toBe('2023');
		const dvf = result.themes.marche.rows.find(row => row.concept.fieldId === 'dvf.prix_m2_median')!;
		expect(dvf.cells[result.column.id]).toMatchObject({ millesime: null, source: { nom: 'Demandes de valeurs foncières (DVF)' } });
		const missing = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, annee_revenus: null }, []);
		expect(missing.synthese.rows[3].cells[missing.column.id]).toMatchObject({ millesime: null, source: null });
	});
	it('uses the known code when the name is absent and retains long names without truncation', () => {
		expect(mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, nom_commune: null }).column.label).toBe('85047');
		const name = 'Commune de test avec un nom volontairement très long sans troncature';
		expect(mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, nom_commune: name }).column.label).toBe(name);
	});
	it('does not infer a sample-threshold reason from a missing DVF median', () => {
		const result = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, prix_m2_median_maison: null, nb_mutations_12m_maison: 1 });
		const price = result.themes.marche.rows.find(row => row.concept.fieldId === 'dvf.prix_m2_median_maison')!;
		const count = result.themes.marche.rows.find(row => row.concept.fieldId === 'dvf.nb_mutations_12m_maison')!;
		expect(price.cells[result.column.id]).toMatchObject({ kind: 'absent', reason: undefined });
		expect(count.cells[result.column.id]).toMatchObject({ kind: 'value', display: '1' });
	});
	it('keeps all four KPI values and five existing themes, including every historical row', () => {
		const result = mapCommuneToGrid(COMMUNE_85047_CHALLANS);
		expect(result.synthese.rows).toHaveLength(4);
		expect(result.fiche.themes.map(theme => theme.key)).toEqual(baseline.synthetic.commune.themes.map(theme => theme.key));
		baseline.synthetic.commune.kpi.forEach((expected, index) => {
			expect(result.fiche.kpi[index]).toMatchObject(expected);
		});
		baseline.synthetic.commune.themes.forEach((expected, index) => {
			for (const row of expected.rows) expect(result.fiche.themes[index].rows).toContainEqual(expect.objectContaining(row));
		});
		expect(ficheDepth(result.fiche.themes)).toEqual(ficheDepth(mapCommuneToFiche(COMMUNE_85047_CHALLANS).themes));
	});
	it('qualifies NULL and zero from the raw KPI input without substituting the sketch rate', () => {
		const empty = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, taux_vacance: null });
		const zero = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, taux_vacance: 0, revenu_median: 0 });
		expect(empty.synthese.rows[2].cells[empty.column.id]).toMatchObject({ kind: 'absent', state: 'out_of_coverage', reason: undefined });
		expect(zero.synthese.rows[2].cells[zero.column.id]).toMatchObject({ kind: 'value', display: '0', measuredZero: true });
		expect(zero.synthese.rows[3].cells[zero.column.id]).toMatchObject({ kind: 'value', measuredZero: false });
		expect(mapCommuneToGrid(COMMUNE_85047_CHALLANS).fiche.kpi[2].value).toBe('9,4');
	});
	it('keeps private-housing and declared-income reservations in the cells and exportable contexts', () => {
		const result = mapCommuneToGrid(COMMUNE_85047_CHALLANS);
		expect(result.synthese.rows[2].cells[result.column.id].reserves.join(' ')).toContain('parc privé');
		expect(result.synthese.rows[3].cells[result.column.id].reserves.join(' ')).toContain('avant redistribution');
		const contexts = result.fiche.themes.flatMap(theme => theme.rows).filter(row => row.kind === 'context');
		expect(contexts.some(row => row.value.includes('parc privé'))).toBe(true);
		expect(contexts.some(row => row.value.includes('avant redistribution'))).toBe(true);
	});
	it('preserves the official rent fixture context at its actual estimation mesh', () => {
		const commune = rentFixture.communes['05066'].donnees[0] as CommuneSynthese;
		const result = mapCommuneToGrid(commune);
		const rent = result.synthese.rows[0].cells[result.column.id];
		// The existing field contract rounds the raw official 9.76 to one decimal.
		expect(getFieldContract('loyers.loyer_m2_appartement').precision.fraction_digits).toBe(1);
		expect(rent).toMatchObject({ kind: 'value', display: '9,8', unit: '€/m²/mois CC', grain: 'Maille' });
		for (const text of ['T3 2025', '52 m²', 'charges comprises', '0 obs. commune / 484 obs. maille', 'prudence']) expect(rent.reserves.join(' ')).toContain(text);
		expect(result.fiche.themes[0].rows.filter(row => row.kind === 'context').map(row => row.value).join(' ')).toContain('0 obs. commune / 484 obs. maille');
	});
	it('keeps inherited export selections and values while adding the visible context rows', () => {
		const result = mapCommuneToGrid(COMMUNE_85047_CHALLANS);
		for (const baselineTheme of baseline.synthetic.commune.themes) {
			const actual = result.fiche.themes.find(theme => theme.key === baselineTheme.key)!;
			const exportRows = actual.rows.filter(row => row.available !== false).map(row => [row.label, row.value]);
			const expected = baselineTheme.rows.filter(row => !('available' in row) || row.available !== false).map(row => [row.label, row.value]);
			expect(exportRows.slice(0, expected.length)).toEqual(expected);
			if (baselineTheme.key === 'environnement') expect(exportRows.at(-1)?.[1]).toContain('avant redistribution');
			if (baselineTheme.key === 'propriete') expect(exportRows.at(-1)?.[1]).toContain('parc privé');
		}
	});
	it('keeps absent rent details unknown and does not inherit a housing identity', () => {
		const result = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, loyer_m2_appartement: 10, loyers_appartement: null });
		const rent = result.synthese.rows[0].cells[result.column.id];
		expect(rent.grain).toBe('Maille d’estimation inconnue');
		expect(rent.reserves.join(' ')).toContain('Précisions inconnues');
		expect(rent.reserves.join(' ')).not.toContain('T3 2025');
		expect(result.column.entity).toBe('commune');
	});
});

describe('Commune adapter wired to the real grid wrappers and inherited CSV helper', () => {
	// The summary grid this case used to mount has no page any more; the unit and absence rules it
	// guarded are read on the theme panel of the commune page.
	it('renders the Challans vacancy rate with its unit and caveat, and omits an undeclared rent absence', async () => {
		const container = panneauDeTheme(COMMUNE_85047_CHALLANS, 'immobilier');
		const vacancy = container.querySelector('[data-field-id="vacance.taux_vacance"]')!;
		expect(vacancy.querySelector(':scope > dd .state-value .value')?.textContent?.trim()).toBe('9,4');
		expect(vacancy.querySelector(':scope > dd .state-value .unit')?.textContent?.trim()).toBe('%');
		await ouvrirVolet(vacancy);
		const limites = [...vacancy.querySelectorAll('.source-panel-entry')]
			.find(node => node.querySelector('dt')?.textContent === 'Limites')?.querySelector('dd')?.textContent;
		expect(limites).toContain('parc privé');
		// The fixture serves the flat apartment rent null without a declared state: the row is not
		// rendered at all, never given a state the API did not declare (D-47).
		expect(container.querySelector('[data-field-id="loyers.loyer_m2_appartement"]')).toBeNull();
	});
	it('renders the rent context and real mesh in its theme without changing the formatted value', async () => {
		const commune = rentFixture.communes['05066'].donnees[0] as CommuneSynthese;
		const container = panneauDeTheme(commune, 'immobilier');
		// Since 4.9 the rents are read in « Prix et ventes », in their group.
		expect(container.querySelector('#critere-loyers [data-field-id="loyers.loyer_m2_appartement"]')?.closest('details.cat')?.id).toBe('q-prix');
		const rent = container.querySelector('[data-field-id="loyers.loyer_m2_appartement"]')!;
		expect(rent.querySelector(':scope > dd .state-value .value')?.textContent?.trim()).toBe('9,8');
		expect(rent.querySelector(':scope > dd .state-value .unit')?.textContent?.trim()).toBe('€/m²/mois');
		// The object of the row is read in its panel (03-17 R-1).
		expect(rent.querySelector('.tab-n')?.textContent).not.toContain('Maille');
		await ouvrirVolet(rent);
		expect([...rent.querySelectorAll('.source-panel-entry')]
			.find((node) => node.querySelector('dt')?.textContent === 'Objet')?.querySelector('dd')?.textContent).toBe('Maille');
		expect(container.textContent).toContain('0 obs. commune / 484 obs. maille');
		expect(container.textContent).toContain('charges comprises');
	});
	it('exports the two new source boundaries and still neutralizes a formula-like inherited value', () => {
		const result = mapCommuneToGrid({ ...COMMUNE_85047_CHALLANS, nom_epci: '=1+1' });
		const captures: string[] = [];
		vi.stubGlobal('Blob', class { constructor(parts: string[]) { captures.push(parts.join('')); } });
		vi.stubGlobal('URL', { createObjectURL: () => 'blob:commune-grid-test', revokeObjectURL: () => {} });
		vi.spyOn(document, 'createElement').mockReturnValue({ click: () => {} } as HTMLAnchorElement);
		for (const theme of result.fiche.themes) {
			// Same existing selection and columns as the route's per-theme export.
			downloadCsv(['Indicateur', 'Valeur'], theme.rows.filter(row => row.available !== false).map(row => [row.label, row.value]), `${theme.key}.csv`);
		}
		expect(captures.join('\n')).toContain('avant redistribution');
		expect(captures.join('\n')).toContain('parc privé');
		expect(captures.join('\n')).toContain("'=1+1");
		expect(captures.join('\n')).not.toContain('EPCI,=1+1');
	});
});

// 03-08 (D-46): the five folded themes regroup the rows the projections already produce. Frozen
// responses: Challans (flat columns) and Nantes (flat columns and dated DVF / DPE blocks).
describe('Commune themes (D-46) — one theme per field, served states, no empty theme', () => {
	const PERIOD_SOURCES: SourceAttribution[] = [...COMMUNE_85191_SOURCES];
	const HISTORICAL_85191: CommuneSynthese = COMMUNE_85191_OBSERVED;
	const periodRows = (commune: CommuneSynthese = HISTORICAL_85191, sources: SourceAttribution[] = PERIOD_SOURCES) =>
		themesCommune(commune, sources).flatMap(theme => theme.lignes);

	it('keeps source periods distinct from imports on the historical 85191 rows', () => {
		const rows = periodRows();
		const find = (field: string) => rows.find(row => row.fieldId === field)!;
		expect(find('connectivite.fibre_pct').periode).toBe('Période source : T2 2026');
		for (const field of ['dpe.dpe_passoires_pct', 'dpe.dpe_median', 'dpe.annee_construction_mediane']) {
			expect(find(field).periode).toBe('Dates des diagnostics non renseignées');
			expect(find(field).periode).not.toContain('2026-10-08');
			expect(find(field).volet).toContainEqual({ terme: 'Dernière importation réussie', valeur: '08/10/2026' });
		}
		expect(find('scolaire.nb_etablissements_scolaires')).toMatchObject({
			libelle: 'Collèges référencés par la sectorisation',
			periode: 'Période de la sectorisation non renseignée', etat: { kind: 'valeur', texte: '4' }
		});
		expect(find('connectivite.fibre_pct').etat).toMatchObject({ kind: 'valeur', texte: '98', unite: '%' });
	});

	it('names both vacancy populations without guessing the private-stock year', () => {
		const rows = periodRows();
		expect(rows.find(row => row.fieldId === 'demographie.nb_logements_vacants_recensement')).toMatchObject({
			libelle: 'Logements vacants au recensement', periode: 'Millésime 2022',
			etat: { kind: 'valeur', texte: '1\u202f870' }
		});
		expect(rows.find(row => row.fieldId === 'vacance.taux_vacance')).toMatchObject({
			libelle: 'Taux de vacance du parc privé',
			periode: 'Vacants : 2026 · année du parc privé non renseignée',
			etat: { kind: 'valeur', texte: '7,59', unite: '%' }
		});
	});

	it('dates a LOVAC count without a denominator caveat and keeps the served rental reference year', () => {
		const rows = periodRows({ ...HISTORICAL_85191, nb_vacants_plus_2ans: 296, annee_loyers: 2025 });
		expect(rows.find(row => row.fieldId === 'vacance.nb_vacants_plus_2ans')).toMatchObject({
			periode: 'Vacants : 2026', etat: { kind: 'valeur', texte: '296' }
		});
		expect(rows.find(row => row.fieldId === 'loyers.annee')?.periode).toBe('Millésime 2025');
	});

	it.each(['ff_pp_total_25', 'ff_pp_total_26', 'pp_total_26'])('presents the proven LOVAC pair %s without column jargon', column => {
		const year = column.endsWith('_25') ? '2025' : '2026';
		const proof = `Logements vacants : 2026 via pp_vacant_26 ; parc privé : ${year} via ${column}`;
		for (const label of [proof, `Libellé archivé : LOVAC 2026 ; ${proof}`]) {
			const sources = PERIOD_SOURCES.map(source => source.dataset === 'vacance' ? {
				...source, periode: { debut: null, fin: null, statut: 'connue' as const, label }
			} : source);
			const row = periodRows(HISTORICAL_85191, sources).find(row => row.fieldId === 'vacance.taux_vacance')!;
			expect(row.periode).toBe(`Logements vacants : 2026 ; parc privé : ${year}`);
			expect(row.periode).not.toMatch(/via|pp_total|pp_vacant|Libellé archivé/);
			expect(row.etat).toMatchObject({ kind: 'valeur', texte: '7,59' });
		}
	});

	it('keeps zero a value with an unknown period and gives every served flat row a period or reason', () => {
		const rows = periodRows({ ...HISTORICAL_85191, dpe_passoires_pct: 0 });
		expect(rows.find(row => row.fieldId === 'dpe.dpe_passoires_pct')).toMatchObject({
			etat: { kind: 'valeur', texte: '0', unite: '%' }, periode: 'Dates des diagnostics non renseignées'
		});
		for (const row of rows.filter(row => row.etat.kind === 'valeur' && row.fieldId)) {
			expect(row.periode, row.fieldId!).toBeTruthy();
		}
	});

	it('does not date a row outside coverage even when the source has a known period', () => {
		const commune = { ...HISTORICAL_85191, fibre_pct: null,
			etats: { ...HISTORICAL_85191.etats, fibre_pct: { etat: 'out_of_coverage' as const, motif: 'dataset_non_heberge' } } };
		const row = themesCommune(commune, PERIOD_SOURCES).flatMap(theme => theme.lignes)
			.find(row => row.fieldId === 'connectivite.fibre_pct')!;
		expect(row.etat).toMatchObject({ kind: 'absence', etat: 'out_of_coverage' });
		expect(row.periode).toBeNull();
	});

	const WITH_ABSENCE = {
		...COMMUNE_85047_CHALLANS,
		prix_m2_median_appartement: null,
		etats: {
			loyer_m2_appartement: { etat: 'not_available', motif: 'Loyer d’appartement non publié pour cette commune.' },
			prix_m2_median_appartement: { etat: 'insufficient_sample', motif: '3 ventes d’appartements sur 12 mois, sous le seuil de publication de 5.' }
		}
	} as CommuneSynthese;

	it('gives every served field exactly one theme, in the mock-up order, each row once', () => {
		for (const commune of [WITH_ABSENCE, COMMUNE_INDICATORS_44 as CommuneSynthese]) {
			const themes = themesCommune(commune);
			expect(themes.map(theme => theme.cle)).toEqual(COMMUNE_THEMES.map(theme => theme.cle).filter(cle => themes.some(theme => theme.cle === cle)));
			const lignes = themes.flatMap(theme => theme.lignes.map(ligne => ({ theme: theme.cle, ligne })));
			expect(new Set(lignes.map(({ ligne }) => ligne.id)).size).toBe(lignes.length);
			for (const { theme, ligne } of lignes) {
				if (ligne.fieldId == null) continue;
				expect(THEME_DU_CHAMP[ligne.fieldId], ligne.fieldId).toBe(theme);
				expect(lignes.filter(({ ligne: other }) => other.fieldId === ligne.fieldId && other.id === ligne.fieldId)).toHaveLength(ligne.id === ligne.fieldId ? 1 : 0);
			}
			for (const theme of themes) expect(theme.lignes.length).toBeGreaterThan(0);
		}
	});

	it('keeps every field the former grids render, except the fields no API column serves', () => {
		// Every null flat column declared, as the API serves it since D-47 (03-06).
		const flat = COMMUNE_INDICATORS_44 as CommuneSynthese;
		const declared = { ...flat, etats: Object.fromEntries(Object.entries(flat)
			.filter(([key, value]) => value == null && !['code_region', 'loyers_appartement', 'loyers_maison', 'etats'].includes(key))
			.map(([key]) => [key, { etat: 'not_reported', motif: 'Aucune ligne de la source pour cette commune.' }])) } as CommuneSynthese;
		for (const key of ['prix_m2_median_maison', 'nb_mutations_12m_maison', 'prix_m2_median_appartement', 'nb_mutations_12m_appartement'] as const) {
			if (declared[key] === undefined) declared.etats![key] = { etat: 'not_reported', motif: 'Aucune ligne de la source pour cette commune.' };
		}
		const grid = mapCommuneToGrid(declared);
		const rendered = new Set(themesCommune(declared).flatMap(theme => theme.lignes.map(ligne => ligne.fieldId)));
		const former = new Set(Object.values(grid.themes).flatMap(g => g.rows.map(row => row.concept.fieldId)).filter((id): id is string => Boolean(id)));
		const missing = [...former].filter(id => !rendered.has(id));
		expect(missing.sort()).toEqual(['commune.propriete.coproprietes', 'commune.risques.risques_naturels']);
		for (const id of former) if (!missing.includes(id)) expect(THEME_DU_CHAMP[id], id).toBeDefined();
	});

	it('shows the absence state the API declared, never the historical out-of-coverage default', () => {
		const immobilier = themesCommune(WITH_ABSENCE).find(theme => theme.cle === 'immobilier')!;
		const prix = immobilier.lignes.find(ligne => ligne.fieldId === 'dvf.prix_m2_median_appartement')!;
		expect(prix.etat).toMatchObject({ kind: 'absence', etat: 'insufficient_sample', motif: '3 ventes d’appartements sur 12 mois, sous le seuil de publication de 5.' });
		const loyer = immobilier.lignes.find(ligne => ligne.fieldId === 'loyers.loyer_m2_appartement')!;
		expect(loyer.etat).toMatchObject({ kind: 'absence', etat: 'not_available' });
		expect(loyer.reserves).toEqual([]);
		expect(loyer.volet.find(entree => entree.terme === 'État et motif')).toBeDefined();
		const zero = themesCommune({ ...WITH_ABSENCE, taux_vacance: 0 }).find(theme => theme.cle === 'immobilier')!
			.lignes.find(ligne => ligne.fieldId === 'vacance.taux_vacance')!;
		expect(zero.etat).toEqual({ kind: 'valeur', texte: '0', unite: '%' });
		expect(zero.objet).toBe('Parc privé communal');
	});

	it('does not render an absence the API did not declare, and names it on the console', () => {
		const error = vi.spyOn(console, 'error').mockImplementation(() => {});
		const themes = themesCommune({ ...COMMUNE_85047_CHALLANS, etats: {} } as CommuneSynthese);
		expect(themes.flatMap(theme => theme.lignes).some(ligne => ligne.fieldId === 'loyers.loyer_m2_appartement')).toBe(false);
		expect(error).toHaveBeenCalledWith(expect.stringContaining('absence non déclarée'));
	});

	it('a field THEME_DU_CHAMP does not place: loud in development, left out in production', () => {
		// A field added to the mapper without its theme entry. Grid copied, the application is untouched.
		const commune = { ...COMMUNE_85047_CHALLANS } as CommuneSynthese;
		const grid = mapCommuneToGrid(commune);
		const environnement = grid.themes.environnement;
		const modele = environnement.rows.find(row => row.concept.fieldId === 'demographie.population')!;
		const inconnu = { ...modele, concept: { ...modele.concept, id: 'commune-environnement:nouveau.champ', fieldId: 'nouveau.champ', label: 'Champ sans thème' } };
		const avecInconnu = { ...grid, themes: { ...grid.themes, environnement: { ...environnement, rows: [...environnement.rows, inconnu] } } };
		vi.spyOn(console, 'error').mockImplementation(() => {});
		expect(() => themesCommune(commune, null, avecInconnu)).toThrow('Commune row without a theme: commune-environnement:nouveau.champ');
		// Production: no exception (no 500 in SSR), the row is left out, every other row is kept.
		vi.stubEnv('DEV', false);
		try {
			const themes = themesCommune(commune, null, avecInconnu);
			const ids = themes.flatMap(theme => theme.lignes.map(ligne => ligne.id));
			expect(ids).not.toContain('nouveau.champ');
			expect(ids).toEqual(themesCommune(commune, null, grid).flatMap(theme => theme.lignes.map(ligne => ligne.id)));
		} finally {
			vi.unstubAllEnvs();
		}
	});

	it('leaves out a theme when none of its fields is served (no empty panel)', () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const sansRisques = { ...COMMUNE_85047_CHALLANS, zone_abc: null, zone_abc_reclassement: null, nb_monuments: null, etats: {} } as CommuneSynthese;
		expect(themesCommune(sansRisques).map(theme => theme.cle)).not.toContain('risques-urbanisme');
		expect(() => panneauDeTheme(sansRisques, 'risques-urbanisme')).toThrow('Missing theme risques-urbanisme');
	});

	it('labels the rows with the served definitions: P10–P90, school map, one ABC zone', () => {
		const themes = themesCommune(COMMUNE_INDICATORS_44 as CommuneSynthese);
		const lignes = themes.flatMap(theme => theme.lignes);
		expect(lignes.map(ligne => ligne.libelle).filter(libelle => /P10–P90/.test(libelle))).toEqual([
			'Intervalle P10–P90 des maisons (fenêtre courante)', 'Intervalle P10–P90 des appartements (fenêtre courante)'
		]);
		expect(lignes.some(ligne => /Q1|Q3/.test(ligne.libelle))).toBe(false);
		expect(lignes.find(ligne => ligne.fieldId === 'scolaire.nb_etablissements_scolaires')?.libelle).toBe('Collèges référencés par la sectorisation');
		expect(lignes.filter(ligne => ligne.fieldId === 'zonage_abc.zone')).toHaveLength(1);
		const interval = lignes.find(ligne => ligne.id === 'dvf-maison-quantiles-courants')!;
		// The three public definitions of P10, P50 and P90, joined (03-17 F66): never the contract descriptions.
		const definitionIntervalle = interval.volet.find(entree => entree.terme === 'Définition');
		expect(definitionIntervalle).toMatchObject({ valeur: expect.stringContaining('10 % des ventes') });
		expect(definitionIntervalle).toMatchObject({ valeur: expect.stringContaining('90 % des ventes') });
		expect(definitionIntervalle).not.toMatchObject({ valeur: expect.stringContaining('10e centile') });
	});

	it('reads a dated-block row with its window and source, and exports its state and caveats', () => {
		const immobilier = themesCommune(COMMUNE_INDICATORS_44 as CommuneSynthese).find(theme => theme.cle === 'immobilier')!;
		const mediane = immobilier.lignes.find(ligne => ligne.id === 'dvf-maison-mediane-courante')!;
		expect(mediane.etat.kind).toBe('valeur');
		expect(mediane.periode).toMatch(/→ .* \(fin exclue\)/);
		expect(mediane.source?.nom).toBeTruthy();
		const [groupe] = themesPourExport([immobilier]);
		const ligne = groupe.rows.find(row => row.id === 'dvf-maison-mediane-courante')!;
		expect(ligne.state).toBe('value');
		expect(ligne.presentation?.reserves?.join(' ')).toContain('Période :');
	});
});
