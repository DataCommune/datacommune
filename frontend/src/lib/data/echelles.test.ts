// Scale table of the fiche (04.8-03; UI-SPEC v4 § 3.5, § 4.3, § 4.5; D-07, D-32): every level the API
// serves has its mark (read from the `NiveauGranularite` Literal of the API code), the rank is a pure
// function, and the zone is the cut the API serves, pinned on `est_de_l_adresse` (resume.py:35-47).
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { GranulariteRapport } from '$lib/api/rapport';
import { NIVEAUX, niveauDe, perimetreSecteur, rangEchelle, zoneDe } from './echelles';

// The repository root: vitest runs from `frontend/` (as registre.test.ts reads the registry).
const RACINE = resolve(process.cwd(), '..');
const NBSP = ' ';

/** The values of `NiveauGranularite = Literal[...]` in the API code. */
function niveauxServis(source = readFileSync(join(RACINE, 'api/backend/app/rapport/schemas.py'), 'utf8')): string[] {
	const bloc = /NiveauGranularite\s*=\s*Literal\[([^\]]*)\]/.exec(source);
	if (!bloc) throw new Error('NiveauGranularite not found in schemas.py');
	return [...bloc[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

const g = (niveau: string, rayon_m: number | null = null, arrondissement: GranulariteRapport['arrondissement'] = null): GranulariteRapport => ({
	niveau,
	rayon_m,
	arrondissement
});

describe('A3/A4 — wording of the served sector list', () => {
	it('keeps communal wording for an empty list or commune entries only', () => {
		expect(perimetreSecteur([])).toBe('pour toute la commune');
		expect(perimetreSecteur([{ granularite: g('commune') }, { granularite: g('commune') }])).toBe('pour toute la commune');
	});
	it.each(['epci', 'departement', 'iris', 'arrondissement', 'rayon', 'niveau_futur'])('says the wider perimeter for %s among communes', (niveau) => {
		expect(perimetreSecteur([{ granularite: g('commune') }, { granularite: g(niveau, niveau === 'rayon' ? 5000 : null) }])).toBe('pour un périmètre plus large que l’adresse');
	});
});

describe('NIVEAUX — chaque niveau servi a son repère (D-07)', () => {
	it('the levels of the API Literal are exactly the keys of NIVEAUX (9)', () => {
		const servis = niveauxServis();
		expect(servis.length).toBe(9);
		expect(new Set(Object.keys(NIVEAUX))).toEqual(new Set(servis));
	});

	it('a truncated copy of the Literal fails the comparison (the reading is not vacuous)', () => {
		const ampute = niveauxServis().filter((n) => n !== 'epci');
		expect(new Set(Object.keys(NIVEAUX))).not.toEqual(new Set(ampute));
		const source = 'NiveauGranularite = Literal[\n    "adresse",\n    "commune",\n]';
		expect(niveauxServis(source)).toEqual(['adresse', 'commune']);
	});

	it('every level has a pictogram, a word, a long form and the rank of § 3.5', () => {
		const rangs = Object.fromEntries(Object.entries(NIVEAUX).map(([n, d]) => [n, d.rang]));
		expect(rangs).toEqual({ adresse: 0, batiment: 1, parcelle: 1, rayon: 2, iris: 3, arrondissement: 4, commune: 5, epci: 6, departement: 7 });
		expect(NIVEAUX.iris.pictogramme).toBe('carreau');
		expect(NIVEAUX.arrondissement.pictogramme).toBe('commune');
		expect(NIVEAUX.epci.pictogramme).toBe('departement');
		for (const d of Object.values(NIVEAUX)) {
			expect(d.mot.trim()).not.toBe('');
			expect(d.longue.trim()).not.toBe('');
		}
	});
});

describe('niveauDe — mot et forme longue', () => {
	it('a radius says its distance, a whole kilometre without « ,0 »', () => {
		expect(niveauDe(g('rayon', 500)).mot).toBe(`500${NBSP}m autour`);
		expect(niveauDe(g('rayon', 1000)).mot).toBe(`1${NBSP}km autour`);
		expect(niveauDe(g('rayon', 5000)).mot).toBe(`5${NBSP}km autour`);
		expect(niveauDe(g('rayon', 500)).longue).toBe(`dans un rayon de 500${NBSP}m autour de l’adresse`);
	});

	it('an arrondissement says its served name in the long form, never a guessed one', () => {
		expect(niveauDe(g('arrondissement', null, { code: '75112', nom: '12e arrondissement de Paris' })).longue).toBe(
			'pour tout le 12e arrondissement de Paris'
		);
		expect(niveauDe(g('arrondissement')).longue).toBe('pour tout l’arrondissement');
		expect(niveauDe(g('arrondissement')).mot).toBe('Arrondissement');
	});

	it('an unknown level: no pictogram, « Échelle : {niveau} », rank 5, no error', () => {
		const r = niveauDe(g('inconnu'));
		expect(r.pictogramme).toBeUndefined();
		expect(r.mot).toBe(`Échelle${NBSP}: inconnu`);
		expect(r.rang).toBe(5);
		expect(zoneDe(g('inconnu'))).toBe('tuile');
	});
});

describe('rangEchelle — fonction pure du niveau et du rayon (§ 4.3)', () => {
	it('ranks every level, then the radii in increasing order', () => {
		const ordre = [g('adresse'), g('batiment'), g('rayon', 500), g('rayon', 1000), g('iris'), g('arrondissement'), g('commune'), g('epci'), g('departement')];
		const rangs = ordre.map(rangEchelle);
		expect(rangs).toEqual([...rangs].sort((a, b) => a - b));
		expect(rangEchelle(g('parcelle'))).toBe(rangEchelle(g('batiment')));
		const rayons = [10000, 500, 5000, 1000].map((r) => g('rayon', r));
		const tries = [...rayons].sort((a, b) => rangEchelle(a) - rangEchelle(b) || (a.rayon_m ?? 0) - (b.rayon_m ?? 0));
		expect(tries.map((x) => x.rayon_m)).toEqual([500, 1000, 5000, 10000]);
	});
});

describe('zoneDe — la coupe servie par l’API (D-32, resume.py:35-47)', () => {
	const source = readFileSync(join(RACINE, 'api/backend/app/rapport/resume.py'), 'utf8');

	it('the API code still cuts at the address, the building, the parcel and 1 000 m', () => {
		expect(source).toMatch(/NIVEAUX_ADRESSE = frozenset\(\{"adresse", "batiment", "parcelle"\}\)/);
		expect(source).toMatch(/RAYON_ADRESSE_MAX_M = 1000\b/);
		expect(source).toMatch(/granularite\.rayon_m <= RAYON_ADRESSE_MAX_M/);
	});

	it('rows: address, building, parcel, radius of 500 and 1 000 m', () => {
		for (const x of [g('adresse'), g('batiment'), g('parcelle'), g('rayon', 500), g('rayon', 1000)]) expect(zoneDe(x), JSON.stringify(x)).toBe('ligne');
	});

	it('tiles: radius of 1 001, 5 000 and 10 000 m, IRIS, arrondissement, commune, EPCI, department, unknown', () => {
		for (const x of [g('rayon', 1001), g('rayon', 5000), g('rayon', 10000), g('iris'), g('arrondissement'), g('commune'), g('epci'), g('departement'), g('inconnu')]) {
			expect(zoneDe(x), JSON.stringify(x)).toBe('tuile');
		}
	});
});
