// Groups of the catalogue (review of 2026-11-11): every served key is read under one group, in the order
// of the address fiche, and a key the table does not know is never lost.
import { describe, expect, it } from 'vitest';
import fixture from './__fixtures__/datasets-api.json';
import { QUESTIONS } from './questions';
import { GROUPES_CATALOGUE, groupeDuJeu, grouperJeux } from './catalogue-groupes';

// The 40 keys served by datacommune.fr on 2026-11-11 (read in the page, `/v1/datasets`).
const CLES_SERVIES_LE_2026_11_11 = [
	'altimetrie', 'annuaire_education', 'antennes', 'axes_transport', 'ban', 'batiments', 'bdnb', 'cadastre',
	'connectivite', 'copropriete', 'delinquance', 'demographie', 'dpe', 'dvf', 'eau_potable', 'encadrement_communes',
	'encadrement_loyers', 'equipements', 'errial', 'filosofi_carreaux', 'fiscalite', 'foncier_pm', 'gares', 'geologie',
	'georisques', 'iris', 'loyers', 'monuments', 'peb', 'permis', 'qualite_air', 'revenus', 'rnb', 'scolaire', 'sirene',
	'transports', 'urbanisme', 'vacance', 'zonage_abc', 'zone_tendue'
];

describe('catalogue-groupes', () => {
	it('opens on the four parts of the address fiche, titled as the fiche titles them, then the territory', () => {
		expect(GROUPES_CATALOGUE.slice(0, 4).map((groupe) => groupe.titre)).toEqual(QUESTIONS.map((partie) => partie.titre));
		expect(GROUPES_CATALOGUE.map((groupe) => groupe.id)).toEqual(['prix', 'logement', 'quotidien', 'risques', 'territoire', 'autres']);
		// Prices, then the DPE: what most readers came for (rule of 2026-11-06).
		expect(GROUPES_CATALOGUE[0].cles[0]).toBe('dvf');
		expect(GROUPES_CATALOGUE[1].cles[0]).toBe('dpe');
	});

	it('classifies every key served on 2026-11-11 and every key of the fixture, each once', () => {
		const classees = GROUPES_CATALOGUE.flatMap((groupe) => groupe.cles);
		expect(new Set(classees).size).toBe(classees.length);
		for (const cle of [...CLES_SERVIES_LE_2026_11_11, ...fixture.donnees.map((jeu) => jeu.nom)]) {
			expect(groupeDuJeu(cle), cle).not.toBe('autres');
		}
		expect([...classees].sort()).toEqual([...CLES_SERVIES_LE_2026_11_11].sort());
	});

	it('keeps a key it does not know, under « Autres sources », last and in the served order', () => {
		const jeux = [{ nom: 'zzz_nouveau' }, { nom: 'geologie' }, { nom: 'aaa_nouveau' }, { nom: 'loyers' }, { nom: 'dvf' }];
		const groupes = grouperJeux(jeux);
		expect(groupes.map(({ groupe, jeux: siens }) => [groupe.titre, siens.map((jeu) => jeu.nom)])).toEqual([
			['Prix et ventes', ['dvf', 'loyers']],
			['Risques', ['geologie']],
			['Autres sources', ['zzz_nouveau', 'aaa_nouveau']]
		]);
		expect(groupes.flatMap(({ jeux: siens }) => siens)).toHaveLength(jeux.length);
	});
});
