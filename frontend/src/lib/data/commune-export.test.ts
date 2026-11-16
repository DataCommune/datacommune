// C1 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui aux trois catégories de la fiche
// commune, export CSV compris »). The CSV export of the commune fiche grouped its rows by the five former
// themes while the page shows three categories. It now follows the page: same rows, same cells, each under
// the category the fiche reads it in. This amends D-35 (« the five themes stay those of the CSV export »).
import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './__fixtures__/commune-85191-20261012';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import { categoriesCommune } from './commune-categories';
import { categoriesPourExport } from './commune-export';
import { themesCommune, themesPourExport } from './commune-grid';

const CAS: [string, CommuneSynthese, SourceAttribution[] | null][] = [
	['La Roche-sur-Yon (observed)', COMMUNE_85191_OBSERVED as CommuneSynthese, [...COMMUNE_85191_SOURCES] as SourceAttribution[]],
	['Challans (without sources)', COMMUNE_85047_CHALLANS, null]
];

describe('categoriesPourExport — the CSV follows the three categories of the fiche (C1)', () => {
	it.each(CAS)('%s: the groups are the categories of the page, in its order', (_nom, synthese, sources) => {
		const themes = themesCommune(synthese, sources);
		expect(categoriesPourExport(themes).map((groupe) => [groupe.key, groupe.name])).toEqual(
			categoriesCommune(themes).map((categorie) => [categorie.id, categorie.titre])
		);
		expect(categoriesPourExport(themes).map((groupe) => groupe.name)).toEqual(['Prix et ventes', 'Logement et DPE', 'Vivre dans la commune']);
	});

	it.each(CAS)('%s: every exported row of the themes is exported once, cell for cell', (_nom, synthese, sources) => {
		const themes = themesCommune(synthese, sources);
		const cle = (ligne: unknown) => JSON.stringify(ligne);
		const avant = themesPourExport(themes).flatMap((groupe) => groupe.rows).map(cle).sort();
		const apres = categoriesPourExport(themes).flatMap((groupe) => groupe.rows).map(cle).sort();
		expect(avant.length).toBeGreaterThan(20);
		expect(apres).toEqual(avant);
	});

	it.each(CAS)('%s: a row and a note are exported under the category that shows them on the page', (_nom, synthese, sources) => {
		const themes = themesCommune(synthese, sources);
		const exportees = categoriesPourExport(themes);
		for (const categorie of categoriesCommune(themes)) {
			const attendus = categorie.groupes.flatMap((groupe) => [...groupe.lignes.filter((ligne) => !ligne.horsExport).map((ligne) => ligne.id), ...groupe.notes.map((note) => note.id)]);
			expect(exportees.find((groupe) => groupe.key === categorie.id)!.rows.map((ligne) => ligne.id), categorie.titre).toEqual(attendus);
		}
	});

	it('the vacancy of the private stock leaves « Immobilier » for « Logement et DPE », where the page reads it', () => {
		const themes = themesCommune(COMMUNE_85191_OBSERVED as CommuneSynthese, [...COMMUNE_85191_SOURCES] as SourceAttribution[]);
		const ancien = themesPourExport(themes).find((groupe) => groupe.rows.some((ligne) => ligne.id === 'vacance.taux_vacance' || /vacance/i.test(ligne.label)));
		const nouveau = categoriesPourExport(themes).find((groupe) => groupe.rows.some((ligne) => /vacance/i.test(ligne.label)));
		expect(ancien?.name).toBe('Immobilier');
		expect(nouveau?.name).toBe('Logement et DPE');
	});
});
