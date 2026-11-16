// Order of presentation and membership of the view of the parcels of the map (04.10-08, UI contract
// § 2.6): pure functions, read by the list and by the dialog, never by Leaflet.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { ParcelleCarte } from '$lib/api/carte-parcelles';
import { bornesDuCadre, dansLaVue, horsVueDuGroupe, ordonnerParcelles, parcellesDeLaVue, tuilesDuCadre, ventesComptees } from './parcel-view';

/** A square parcel of *cote* degrees whose south-west corner is (*lon*, *lat*), label point at its centre. */
function carre(id: string, lon: number, lat: number, cote = 0.0002, ventes: number | null = 0): ParcelleCarte {
	const anneau: [number, number][] = [
		[lon, lat],
		[lon + cote, lat],
		[lon + cote, lat + cote],
		[lon, lat + cote],
		[lon, lat]
	];
	return {
		type: 'Feature',
		id,
		geometry: { type: 'Polygon', coordinates: [anneau] },
		properties: { id_parcelle: id, contenance: 100, etiquette: [lon + cote / 2, lat + cote / 2], ventes }
	};
}

/** A parcel of the contract example shape: a multipolygon, its label point given. */
function servie(id: string, etiquette: [number, number], ventes: number | null = 0): ParcelleCarte {
	const [lon, lat] = etiquette;
	return {
		type: 'Feature',
		id,
		geometry: {
			type: 'MultiPolygon',
			coordinates: [[[[lon - 0.0001, lat - 0.0001], [lon + 0.0001, lat - 0.0001], [lon + 0.0001, lat + 0.0001], [lon - 0.0001, lat + 0.0001], [lon - 0.0001, lat - 0.0001]]]]
		},
		properties: { id_parcelle: id, contenance: 100, etiquette, ventes }
	};
}

const ids = (parcelles: ParcelleCarte[]) => parcelles.map((p) => p.id);

describe('ordonnerParcelles', () => {
	const point = { lon: -3.5, lat: 46.5 };

	it('puts the parcel of the address first, then the nearest label point to the farthest', () => {
		const parcelles = [
			servie('00000000ZA0009', [-3.501, 46.5]),
			servie('00000000ZA0003', [-3.5, 46.50028]),
			servie('00000000ZA0001', [-3.5, 46.5]),
			servie('00000000ZA0005', [-3.5, 46.5006])
		];
		expect(ids(ordonnerParcelles(parcelles, '00000000ZA0009', point))).toEqual([
			'00000000ZA0009',
			'00000000ZA0001',
			'00000000ZA0003',
			'00000000ZA0005'
		]);
	});

	it('measures on the ground, not in degrees: a degree of longitude shrinks with the latitude', () => {
		// At 60° north a degree of longitude is half a degree of latitude: 0.0003° east is about 17 m,
		// 0.0002° north about 22 m. In raw degrees the order would be the other way round.
		const nord = { lon: 5, lat: 60 };
		const parcelles = [servie('00000000AB0001', [5, 60.0002]), servie('00000000AB0002', [5.0003, 60])];
		expect(ids(ordonnerParcelles(parcelles, null, nord))).toEqual(['00000000AB0002', '00000000AB0001']);
	});

	it('breaks a tie by the reference, whatever the side and the float noise', () => {
		// The parcels of the contract example: ZA0002 east and ZA0004 west, at the same distance.
		const parcelles = [
			servie('00000000ZA0004', [-3.50045, 46.5]),
			servie('00000000ZA0002', [-3.49955, 46.5]),
			servie('00000000ZA0003', [-3.5, 46.50028]),
			servie('00000000ZA0001', [-3.5, 46.5])
		];
		expect(ids(ordonnerParcelles(parcelles, '00000000ZA0001', point))).toEqual([
			'00000000ZA0001',
			'00000000ZA0003',
			'00000000ZA0002',
			'00000000ZA0004'
		]);
		expect(ids(ordonnerParcelles([...parcelles].reverse(), '00000000ZA0001', point))).toEqual([
			'00000000ZA0001',
			'00000000ZA0003',
			'00000000ZA0002',
			'00000000ZA0004'
		]);
	});

	it('without a parcel of the address (or one not in the list), the distance alone decides', () => {
		const parcelles = [servie('00000000ZA0005', [-3.5, 46.5006]), servie('00000000ZA0003', [-3.5, 46.50028])];
		expect(ids(ordonnerParcelles(parcelles, null, point))).toEqual(['00000000ZA0003', '00000000ZA0005']);
		expect(ids(ordonnerParcelles(parcelles, '00000000ZZ9999', point))).toEqual(['00000000ZA0003', '00000000ZA0005']);
	});

	it('returns a new list and leaves the given one as it was', () => {
		const parcelles = [servie('00000000ZA0005', [-3.5, 46.5006]), servie('00000000ZA0003', [-3.5, 46.50028])];
		const avant = ids(parcelles);
		const ordonnees = ordonnerParcelles(parcelles, null, point);
		expect(ordonnees).not.toBe(parcelles);
		expect(ids(parcelles)).toEqual(avant);
	});
});

describe('dansLaVue', () => {
	const vue = { ouest: 0, sud: 0, est: 0.001, nord: 0.001 };

	it('a parcel inside the view, or across one of its edges, is in it', () => {
		expect(dansLaVue(carre('00000000AA0001', 0.0004, 0.0004), vue)).toBe(true);
		expect(dansLaVue(carre('00000000AA0002', -0.0001, 0.0004), vue)).toBe(true);
		expect(dansLaVue(carre('00000000AA0003', 0.0009, 0.0009), vue)).toBe(true);
	});

	it('a parcel whose label point is out of the view but whose outline crosses it is in it', () => {
		const large = carre('00000000AA0004', -0.0009, 0.0004, 0.001);
		expect(large.properties.etiquette[0]).toBeLessThan(vue.ouest);
		expect(dansLaVue(large, vue)).toBe(true);
	});

	it('a parcel wholly out of the view, on any side, is not in it', () => {
		expect(dansLaVue(carre('00000000AA0005', 0.0011, 0.0004), vue)).toBe(false);
		expect(dansLaVue(carre('00000000AA0006', -0.0003, 0.0004), vue)).toBe(false);
		expect(dansLaVue(carre('00000000AA0007', 0.0004, 0.0011), vue)).toBe(false);
		expect(dansLaVue(carre('00000000AA0008', 0.0004, -0.0003), vue)).toBe(false);
	});

	it('a multipolygon is in the view when one of its parts is', () => {
		const loin = carre('00000000AA0009', 0.005, 0.005).geometry.coordinates as [number, number][][];
		const pres = carre('00000000AA0009', 0.0004, 0.0004).geometry.coordinates as [number, number][][];
		const parcelle: ParcelleCarte = {
			...carre('00000000AA0009', 0.005, 0.005),
			geometry: { type: 'MultiPolygon', coordinates: [loin, pres] }
		};
		expect(dansLaVue(parcelle, vue)).toBe(true);
		expect(dansLaVue({ ...parcelle, geometry: { type: 'MultiPolygon', coordinates: [loin] } }, vue)).toBe(false);
	});

	it('parcellesDeLaVue keeps the parcels of the view, in the given order', () => {
		const parcelles = [carre('00000000AA0002', 0.0005, 0.0005), carre('00000000AA0001', 0.01, 0.01), carre('00000000AA0003', 0.0001, 0.0001)];
		expect(ids(parcellesDeLaVue(parcelles, vue))).toEqual(['00000000AA0002', '00000000AA0003']);
		expect(parcellesDeLaVue([], vue)).toEqual([]);
	});
});

describe('ventesComptees', () => {
	it('counts the parcels with at least one sale, zero included', () => {
		expect(ventesComptees([carre('00000000AA0001', 0, 0, 0.0002, 4), carre('00000000AA0002', 0, 0, 0.0002, 0), carre('00000000AA0003', 0, 0, 0.0002, 1)])).toBe(2);
		expect(ventesComptees([carre('00000000AA0002', 0, 0, 0.0002, 0)])).toBe(0);
		expect(ventesComptees([])).toBe(0);
	});

	it('a parcel whose sales were not counted makes the count unknown, never a smaller one', () => {
		expect(ventesComptees([carre('00000000AA0001', 0, 0, 0.0002, 4), carre('00000000AA0002', 0, 0, 0.0002, null)])).toBeNull();
	});
});

// 04.10-09: the parcels of a shown group the map leaves out of its view (T-26). Those served out of the
// bounds of the address are never drawn and always count; a served one counts when its outline does not
// meet the view. Synthetic bounds, no Leaflet.
describe('horsVueDuGroupe', () => {
	const vue = { ouest: 0, sud: 0, est: 0.001, nord: 0.001 };
	const servieDuGroupe = (id: string, lon: number, lat: number) => {
		const p = carre(id, lon, lat);
		return { id_parcelle: id, geometry: p.geometry, etiquette: p.properties.etiquette };
	};

	it('counts the served parcels out of the view; those out of the bounds of the address are said apart (T-46, S2 of 2026-11-15)', () => {
		const groupe = {
			hors_emprise: 3,
			parcelles: [
				servieDuGroupe('00000000AA0001', 0.0004, 0.0004),
				servieDuGroupe('00000000AA0002', 0.0011, 0.0004),
				servieDuGroupe('00000000AA0003', 0.0004, -0.0003),
				// Across the west edge: its outline meets the view, it is in it.
				servieDuGroupe('00000000AA0004', -0.0001, 0.0004)
			]
		};
		expect(horsVueDuGroupe(groupe, vue)).toBe(2);
	});

	it('every served parcel in the view and none out of the bounds: zero', () => {
		const groupe = { hors_emprise: 0, parcelles: [servieDuGroupe('00000000AA0001', 0.0002, 0.0002), servieDuGroupe('00000000AA0002', 0.0006, 0.0006)] };
		expect(horsVueDuGroupe(groupe, vue)).toBe(0);
	});

	it('follows the view: the same group seen from elsewhere counts other parcels', () => {
		const groupe = { hors_emprise: 1, parcelles: [servieDuGroupe('00000000AA0001', 0.0004, 0.0004), servieDuGroupe('00000000AA0002', 0.0024, 0.0004)] };
		expect(horsVueDuGroupe(groupe, vue)).toBe(1);
		expect(horsVueDuGroupe(groupe, { ouest: 0.002, sud: 0, est: 0.003, nord: 0.001 })).toBe(1);
		expect(horsVueDuGroupe(groupe, { ouest: 0, sud: 0, est: 0.003, nord: 0.001 })).toBe(0);
	});

	it('a group served without its parcels (beyond the cap of the route) is never counted', () => {
		expect(horsVueDuGroupe({ hors_emprise: 12, parcelles: null }, vue)).toBeNull();
	});
});

// 04.10-09: without the map, the tiles of parcels that cover the frame of the opening (centred on the
// address, zoom 18, the size of the frame), and its bounds. Web Mercator, without Leaflet.
describe('tuilesDuCadre', () => {
	// Reference case, read once by playing the opening of the map (e2e `carte-parcelles-groupe`, 375 px
	// wide, frame of 375 x 420 px): the tiles of parcels Leaflet 1.9.4 asked for this centre.
	it('gives the tiles Leaflet asks for the same view (reference case)', () => {
		const tuiles = tuilesDuCadre({ lon: -3.5, lat: 46.5 }, 18, { largeur: 375, hauteur: 420 });
		expect([...tuiles].sort()).toEqual([
			'18/128522/92733',
			'18/128522/92734',
			'18/128522/92735',
			'18/128523/92733',
			'18/128523/92734',
			'18/128523/92735',
			'18/128524/92733',
			'18/128524/92734',
			'18/128524/92735'
		]);
	});

	it('a frame inside one tile asks that tile only; a wider frame asks the tiles around it', () => {
		// The centre of tile 18/128523/92734: a frame of 100 px stays inside it.
		const n = 256 * 2 ** 18;
		const lon = ((128523 + 0.5) / n) * 256 * 360 - 180;
		const y = ((92734 + 0.5) * 256) / n;
		const lat = (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
		expect(tuilesDuCadre({ lon, lat }, 18, { largeur: 100, hauteur: 100 })).toEqual(['18/128523/92734']);
		expect(tuilesDuCadre({ lon, lat }, 18, { largeur: 600, hauteur: 300 })).toHaveLength(9);
	});

	it('never asks a tile twice', () => {
		const tuiles = tuilesDuCadre({ lon: 2.35, lat: 48.85 }, 18, { largeur: 1440, hauteur: 520 });
		expect(new Set(tuiles).size).toBe(tuiles.length);
	});
});

describe('bornesDuCadre', () => {
	it('the frame centred on the point, as wide as the frame at that zoom', () => {
		const bornes = bornesDuCadre({ lon: -3.5, lat: 46.5 }, 18, { largeur: 375, hauteur: 420 });
		expect(bornes.ouest).toBeLessThan(-3.5);
		expect(bornes.est).toBeGreaterThan(-3.5);
		expect(bornes.sud).toBeLessThan(46.5);
		expect(bornes.nord).toBeGreaterThan(46.5);
		expect(bornes.est - bornes.ouest).toBeCloseTo((375 * 360) / (256 * 2 ** 18), 9);
		expect((bornes.est + bornes.ouest) / 2).toBeCloseTo(-3.5, 9);
	});

	it('holds the parcels of the contract example, and not a parcel 400 m east', () => {
		const bornes = bornesDuCadre({ lon: -3.5, lat: 46.5 }, 18, { largeur: 375, hauteur: 420 });
		expect(dansLaVue(servie('00000000ZA0004', [-3.50045, 46.5]), bornes)).toBe(true);
		expect(dansLaVue(servie('00000000ZA0003', [-3.5, 46.50028]), bornes)).toBe(true);
		expect(dansLaVue(servie('00000000ZB0001', [-3.495, 46.5]), bornes)).toBe(false);
	});
});

describe('parcel-view.ts', () => {
	it('is pure: it never imports Leaflet', () => {
		const source = readFileSync('src/lib/utils/parcel-view.ts', 'utf8');
		expect(source).not.toMatch(/from\s+['"]leaflet['"]|import\(\s*['"]leaflet['"]\s*\)|chargeur-carte/);
	});
});
