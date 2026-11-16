import { describe, expect, it } from 'vitest';
import {
	communeEtSonArrondissement,
	communeParente,
	estArrondissement,
	grouperParCommune,
	libelleArrondissement,
	libelleCourtArrondissement,
	mentionArrondissement,
	refusCommuneEtArrondissement,
	TITRE_COMMUNE_ET_ARRONDISSEMENT
} from './arrondissements';

describe('arrondissements — identity read from the code (45 zones)', () => {
	it.each([
		['75101', '75056', '1er arrondissement', '1er arrondissement de Paris'],
		['75112', '75056', '12e arrondissement', '12e arrondissement de Paris'],
		['75120', '75056', '20e arrondissement', '20e arrondissement de Paris'],
		['69381', '69123', '1er arrondissement', '1er arrondissement de Lyon'],
		['69389', '69123', '9e arrondissement', '9e arrondissement de Lyon'],
		['13201', '13055', '1er arrondissement', '1er arrondissement de Marseille'],
		['13216', '13055', '16e arrondissement', '16e arrondissement de Marseille']
	])('%s belongs to %s: « %s » / « %s »', (code, parent, court, long) => {
		expect(communeParente(code)?.code).toBe(parent);
		expect(libelleCourtArrondissement(code)).toBe(court);
		expect(libelleArrondissement(code)).toBe(long);
	});

	it.each(['75056', '69123', '13055', '75121', '75100', '69380', '69390', '13217', '13200', '44109', '2A004', 'abc'])(
		'%s is not an arrondissement',
		(code) => {
			expect(communeParente(code)).toBeNull();
			expect(libelleArrondissement(code)).toBeNull();
		}
	);

	it('45 arrondissements in all', () => {
		let n = 0;
		for (let c = 0; c < 100000; c++) if (communeParente(String(c).padStart(5, '0'))) n++;
		expect(n).toBe(45);
	});
});

describe('arrondissements — projection of the served fields', () => {
	it('only the served « arrondissement » marks a row; an unmarked row is a commune', () => {
		expect(estArrondissement({ type_zone: 'arrondissement' })).toBe(true);
		expect(estArrondissement({ type_zone: 'commune' })).toBe(false);
		expect(estArrondissement({})).toBe(false);
		expect(estArrondissement({ type_zone: null })).toBe(false);
	});

	it('mention in a list: the served parent, else the code, none for a commune', () => {
		expect(mentionArrondissement({ code_insee: '75112', type_zone: 'arrondissement', commune_parent: '75056' })).toBe('Arrondissement de Paris');
		expect(mentionArrondissement({ code_insee: '69383', type_zone: 'arrondissement' })).toBe('Arrondissement de Lyon');
		expect(mentionArrondissement({ code_insee: '75056', type_zone: 'commune', commune_parent: null })).toBeNull();
	});
});

describe('arrondissements — directory grouped under the commune', () => {
	const ligne = (code_insee: string, extra: Record<string, unknown> = {}) => ({ code_insee, ...extra });
	const arr = (code: string, parent = '75056') => ligne(code, { type_zone: 'arrondissement' as const, commune_parent: parent });

	it('places each arrondissement after its commune, in code order, the commune keeping its place', () => {
		const rangees = grouperParCommune([ligne('75056'), ligne('69123'), arr('75112'), arr('75101'), arr('69383', '69123')]);
		expect(rangees.map((r) => [r.ligne.code_insee, r.sous])).toEqual([
			['75056', false],
			['75101', true],
			['75112', true],
			['69123', false],
			['69383', true]
		]);
	});

	it('an arrondissement without its commune on the page is kept once, in place, with its commune named', () => {
		const rangees = grouperParCommune([ligne('69123'), arr('75112')]);
		expect(rangees).toHaveLength(2);
		expect(rangees[1]).toMatchObject({ sous: false, communeAbsente: { code: '75056', nom: 'Paris' } });
	});

	it('every row is returned exactly once (no commune is added to its arrondissements)', () => {
		const entree = [ligne('75056'), arr('75101'), arr('75102'), ligne('44109')];
		const sortie = grouperParCommune(entree).map((r) => r.ligne.code_insee);
		expect([...sortie].sort()).toEqual(entree.map((r) => r.code_insee).sort());
	});

	it('a list without any arrondissement keeps its order', () => {
		expect(grouperParCommune([ligne('44109'), ligne('35238')]).map((r) => r.ligne.code_insee)).toEqual(['44109', '35238']);
	});
});

describe('arrondissements — comparator: a whole and its part', () => {
	it('detects a commune with one of its arrondissements, in any order', () => {
		expect(communeEtSonArrondissement(['75056', '44109', '75112'])).toEqual({ commune: '75056', arrondissement: '75112' });
		expect(communeEtSonArrondissement(['75112', '75056'])).toEqual({ commune: '75056', arrondissement: '75112' });
	});

	it('accepts two arrondissements, or an arrondissement and another commune', () => {
		expect(communeEtSonArrondissement(['75112', '75113'])).toBeNull();
		expect(communeEtSonArrondissement(['75112', '69123'])).toBeNull();
		expect(communeEtSonArrondissement(['75112', '44109'])).toBeNull();
	});

	it('a 422 on that pair is worded with the two zones; the server text is not shown', () => {
		const refus = refusCommuneEtArrondissement(422, 'texte serveur', ['75056', '75112']);
		expect(refus?.titre).toBe(TITRE_COMMUNE_ET_ARRONDISSEMENT);
		expect(refus?.message).toContain('Paris et son 12e arrondissement ne se comparent pas');
		expect(refus?.message).not.toContain('texte serveur');
		expect(refus?.conflit).toEqual({ commune: '75056', arrondissement: '75112' });
	});

	it('a 422 that names an arrondissement without a pair in the link keeps the clear title', () => {
		const refus = refusCommuneEtArrondissement(422, 'Une commune ne se compare pas a son arrondissement', ['44109', '35238']);
		expect(refus?.titre).toBe(TITRE_COMMUNE_ET_ARRONDISSEMENT);
		expect(refus?.conflit).toBeNull();
	});

	it('any other refusal is left to the generic wording', () => {
		expect(refusCommuneEtArrondissement(422, 'codes invalides', ['44109', '35238'])).toBeNull();
		expect(refusCommuneEtArrondissement(503, null, ['75056', '75112'])).toBeNull();
		expect(refusCommuneEtArrondissement(null, null, ['75056', '75112'])).toBeNull();
	});
});
