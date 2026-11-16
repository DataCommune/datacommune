// C1 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui aux trois catégories de la fiche
// commune, export CSV compris »). The comparator of communes showed « Repères » then the five former themes
// (Immobilier, Énergie & bâti, Services & transports, Contexte communal, Risques & urbanisme); it now shows
// « Repères » then the three categories of the commune fiche, each row where the fiche puts it. Nothing else
// moves: the same criteria are displayed, with the same cells.
import { describe, expect, it } from 'vitest';
import type { ReponseApiComparaison } from '$lib/api/commune';
import { categoriesCommune } from './commune-categories';
import { themesCommune } from './commune-grid';
import { composeComparaison } from './commune-comparison';
import comparaisons from './__fixtures__/commune-comparaison.json';

const REPONSES = comparaisons as unknown as Record<string, ReponseApiComparaison>;
const modele = (cle: string) => {
	const reponse = structuredClone(REPONSES[cle]);
	return composeComparaison(reponse.donnees, reponse.meta?.source ?? null);
};

describe('composeComparaison — the three categories of the commune fiche (C1)', () => {
	it('groups the rows as the fiche does: « Repères », then Prix et ventes, Logement et DPE, Vivre dans la commune', () => {
		expect(modele('deux_communes_hebergees').groupes.map((groupe) => [groupe.cle, groupe.titre])).toEqual([
			['reperes', 'Repères'],
			['q-prix', 'Prix et ventes'],
			['q-logement', 'Logement et DPE'],
			['q-quotidien', 'Vivre dans la commune']
		]);
	});

	it('puts each row in the category the fiche of its commune puts it in, in the order of that category', () => {
		const reponse = structuredClone(REPONSES.deux_communes_hebergees);
		const sources = reponse.meta?.source ?? null;
		const groupes = composeComparaison(reponse.donnees, sources).groupes;
		const reperes = new Set(groupes[0].lignes.map((ligne) => ligne.ligne));
		const fiche = categoriesCommune(themesCommune(reponse.donnees.communes[0].synthese!, sources));
		for (const categorie of fiche) {
			const attendues = categorie.groupes.flatMap((groupe) => groupe.lignes.map((ligne) => ligne.id));
			const affichees = [...new Set(groupes.find((groupe) => groupe.cle === categorie.id)!.lignes.map((ligne) => ligne.ligne))];
			// A row shown in « Repères » is not shown twice; every other row keeps the place and order of the fiche.
			expect(affichees, categorie.titre).toEqual(attendues.filter((id) => affichees.includes(id)));
			expect(affichees.filter((id) => reperes.has(id)), categorie.titre).toEqual([]);
		}
	});

	// Counted on the frozen harness response before the change: the same criteria, none lost, none added.
	it.each([
		['deux_communes_hebergees', 89, 77],
		['trois_communes_dont_hors_couverture', 89, 0]
	])('%s: the same criteria are displayed as before, each once', (cle, servis, identiques) => {
		const reponse = REPONSES[cle];
		const m = modele(cle);
		const lignes = m.groupes.flatMap((groupe) => groupe.lignes);
		expect(reponse.donnees.criteres).toHaveLength(servis);
		expect(new Set(lignes.map((ligne) => ligne.cle)).size).toBe(lignes.length);
		expect(lignes.length + m.criteresNonAffiches.length).toBe(servis);
		expect(lignes.length).toBe(LIGNES_AFFICHEES[cle]);
		expect(reponse.donnees.criteres.filter((critere) => critere.identiques)).toHaveLength(identiques);
	});
});

/** Rows displayed by the comparator on each frozen response, read on `claude/integration-1110` before C1. */
const LIGNES_AFFICHEES: Record<string, number> = {
	deux_communes_hebergees: 62,
	trois_communes_dont_hors_couverture: 62
};

// C5 (inventory of 2026-11-10; « price, m², rent, DPE first », reading priority of 2026-11-06): the rents
// were read far down, among the rows of « Prix et ventes ». They join « Repères », after the prices and
// their trend, before the DPE. Each row is still shown once, with the same cells.
describe('composeComparaison — the rents are landmarks (C5)', () => {
	const LOYERS = ['loyers.loyer_m2_appartement', 'loyers.loyer_m2_maison'];
	const champs = (cle: string, groupe: string) => [...new Set(modele(cle).groupes.find((item) => item.cle === groupe)!.lignes.map((ligne) => ligne.fieldId))];

	it.each(['deux_communes_hebergees', 'trois_communes_dont_hors_couverture'])('%s: « Repères » holds both rents, after the DVF rows and before the DPE rows', (cle) => {
		const reperes = champs(cle, 'reperes');
		expect(reperes.filter((champ) => champ.startsWith('loyers.')).sort()).toEqual([...LOYERS].sort());
		const rang = (prefixe: string) => reperes.map((champ, index) => (champ.startsWith(prefixe) ? index : -1)).filter((index) => index >= 0);
		expect(Math.max(...rang('dvf.'))).toBeLessThan(Math.min(...rang('loyers.')));
		expect(Math.max(...rang('loyers.'))).toBeLessThan(Math.min(...rang('dpe.')));
	});

	it('the rents are no longer repeated in « Prix et ventes »', () => {
		expect(champs('deux_communes_hebergees', 'q-prix').filter((champ) => LOYERS.includes(champ))).toEqual([]);
	});
});
