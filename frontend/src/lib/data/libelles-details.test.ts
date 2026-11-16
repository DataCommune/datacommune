import { describe, expect, it } from 'vitest';
import { aplatirDetails, codesSansLibelle, valeursBrutes, type NoeudDetail } from './adresse-rapport';
import { VALEURS_LISIBLES } from './libelles-details';
import reels from './__fixtures__/rapport-details-reels-20261112.json';

// Guard of the details labels: every code served in the details of a theme has its words, or is a
// single word that reads as is. Read on the live site (42 themes, five town halls, 2026-11-12).
const echantillons = Object.entries(reels.themes as Record<string, Record<string, unknown>[]>).flatMap(([id, liste]) =>
	liste.map((details) => ({ id, details }))
);

const libelles = (noeuds: NoeudDetail[]): string[] =>
	noeuds.flatMap((n) => [n.libelle, ...(n.type === 'table' ? n.colonnes : []), ...(n.type === 'groupe' ? libelles(n.enfants) : [])]);

describe('details labels', () => {
	it('reads the real fixture', () => {
		expect(echantillons.length).toBeGreaterThan(100);
	});

	it('serves no raw label on the real details', () => {
		const bruts = new Map<string, string[]>();
		for (const { id, details } of echantillons) for (const code of codesSansLibelle(details, id)) bruts.set(code, [...(bruts.get(code) ?? []), id]);
		expect(Object.fromEntries(bruts)).toEqual({});
	});

	it('the three labels seen online read as words', () => {
		const tout = echantillons.flatMap(({ id, details }) => libelles(aplatirDetails(details, 0, false, id)));
		for (const brut of ['Documents dpe', 'Exception copropriete', 'Batiments parcelle']) expect(tout).not.toContain(brut);
	});

	it('detects a code without words', () => {
		expect(codesSansLibelle({ cle_inconnue_zz: 1, motif: 'motif_inconnu_zz', commune: 'x' })).toEqual(['cle_inconnue_zz', 'motif_inconnu_zz']);
	});
});

// Guard of the one-word values (2026-11-13): a value served as a lone lowercase word (« value »,
// « agglomeration », « moyen ») reaches the details as is. Each one found on the real details is listed
// in `VALEURS_LISIBLES` or written in words by `VALEURS_LIBELLEES`; any other one fails here before it reaches the page.
describe('one-word values of the details', () => {
	it('serves no raw one-word value outside the readable list', () => {
		const bruts = new Map<string, Set<string>>();
		for (const { id, details } of echantillons) {
			for (const valeur of valeursBrutes(details, id)) bruts.set(valeur, (bruts.get(valeur) ?? new Set()).add(id));
		}
		const hors = [...bruts.keys()].filter((v) => !VALEURS_LISIBLES.has(v)).sort();
		expect(Object.fromEntries(hors.map((v) => [v, [...bruts.get(v)!]]))).toEqual({});
		// The list holds only what is still served raw: a value given its words leaves it.
		expect([...VALEURS_LISIBLES].filter((v) => !bruts.has(v)).sort()).toEqual([]);
	});

	it('detects a one-word value at every depth, and not what the page writes itself', () => {
		expect(
			valeursBrutes({
				etat: 'zzvaleur',
				groupe: { niveau: 'zzniveau', liste: ['zzitem'] },
				lignes: [{ type: 'zzcellule', nom: 'Rue des Lilas' }],
				present: true,
				nombre: 3,
				date: '2026-11-13',
				motif: 'motif_inconnu_zz'
			})
		).toEqual(['zzcellule', 'zzitem', 'zzniveau', 'zzvaleur']);
	});
});
