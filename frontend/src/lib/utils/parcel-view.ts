// The parcels of the view of the map and their order of presentation (04.10 UI contract § 2.6). Pure
// functions, without Leaflet: the list reads them, the dialog hands them the bounds of its view.
//
// The order is the parcel of the address, then the nearest label point to the farthest from the point
// of the address. The distance serves this order only and is never shown; it is measured on the ground
// (a degree of longitude shrinks with the latitude) and rounded to the centimetre, so that two parcels
// at the same distance on either side are told apart by their reference, not by float noise.
import type { GroupeVendu, ParcelleCarte } from '$lib/api/carte-parcelles';

export interface BornesVue {
	ouest: number;
	sud: number;
	est: number;
	nord: number;
}

const METRES_PAR_DEGRE = 111_320;

/** Ground distance from *point* to the served label point of *p*, in centimetres. */
function distanceCm(p: ParcelleCarte, point: { lon: number; lat: number }): number {
	const [lon, lat] = p.properties.etiquette;
	const dx = (lon - point.lon) * Math.cos((point.lat * Math.PI) / 180);
	const dy = lat - point.lat;
	return Math.round(Math.hypot(dx, dy) * METRES_PAR_DEGRE * 100);
}

/** The parcel of the address first, then by distance to *point*, ties by reference. A new list. */
export function ordonnerParcelles(parcelles: readonly ParcelleCarte[], idAdresse: string | null, point: { lon: number; lat: number }): ParcelleCarte[] {
	const distances = new Map(parcelles.map((p) => [p.id, distanceCm(p, point)]));
	return [...parcelles].sort((a, b) => {
		if (a.id === idAdresse || b.id === idAdresse) return a.id === idAdresse ? -1 : 1;
		return distances.get(a.id)! - distances.get(b.id)! || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
	});
}

/** Every ring of a geometry, whatever its type. */
function anneaux(geometrie: ParcelleCarte['geometry']): [number, number][][] {
	return geometrie.type === 'Polygon' ? geometrie.coordinates : geometrie.coordinates.flat();
}

/** The parcel is in the view when the box of its outline meets the bounds of the view. */
export function dansLaVue(p: ParcelleCarte, vue: BornesVue): boolean {
	return contourDansLaVue(p.geometry, vue);
}

function contourDansLaVue(geometrie: ParcelleCarte['geometry'], vue: BornesVue): boolean {
	let [ouest, sud, est, nord] = [Infinity, Infinity, -Infinity, -Infinity];
	for (const anneau of anneaux(geometrie)) {
		for (const [lon, lat] of anneau) {
			ouest = Math.min(ouest, lon);
			est = Math.max(est, lon);
			sud = Math.min(sud, lat);
			nord = Math.max(nord, lat);
		}
	}
	return ouest <= vue.est && est >= vue.ouest && sud <= vue.nord && nord >= vue.sud;
}

/**
 * Parcels of a shown group out of the view (T-26, T-48 without the map): the served ones whose outline does
 * not meet the view. Those out of the bounds of the address, never drawn, are said apart (T-46, S2 of
 * 2026-11-15) and not counted again here. Null when the group was served without its parcels (beyond the
 * cap of the route): it is said, never drawn nor counted.
 */
export function horsVueDuGroupe(groupe: Pick<GroupeVendu, 'hors_emprise' | 'parcelles'>, vue: BornesVue): number | null {
	if (!groupe.parcelles) return null;
	return groupe.parcelles.filter((p) => !contourDansLaVue(p.geometry, vue)).length;
}

// Without the map (04.10-09): the frame of the opening, centred on the address, read without Leaflet. The
// projection is the spherical Web Mercator of the tiles (EPSG:3857), in pixels of 256 px tiles; the tiles
// are those Leaflet asks for the same view: the centre floored to the pixel, half the frame on each side.
const TAILLE_TUILE = 256;
const LATITUDE_MAX = 85.0511287798;

interface TailleCadre {
	largeur: number;
	hauteur: number;
}

/** Pixel of *point* at *zoom*, in the plane of the tiles. */
function pixel(point: { lon: number; lat: number }, zoom: number): { x: number; y: number } {
	const n = TAILLE_TUILE * 2 ** zoom;
	const lat = Math.max(-LATITUDE_MAX, Math.min(LATITUDE_MAX, point.lat));
	const sinus = Math.sin((lat * Math.PI) / 180);
	return { x: (n * (point.lon + 180)) / 360, y: n * (0.5 - Math.log((1 + sinus) / (1 - sinus)) / (4 * Math.PI)) };
}

/** Point of the plane of the tiles at *zoom* back to longitude and latitude. */
function point(x: number, y: number, zoom: number): { lon: number; lat: number } {
	const n = TAILLE_TUILE * 2 ** zoom;
	return { lon: (x / n) * 360 - 180, lat: (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI };
}

/** The tiles `z/x/y` that cover a frame of *taille* centred on *centre* at *zoom*, each once. */
export function tuilesDuCadre(centre: { lon: number; lat: number }, zoom: number, taille: TailleCadre): string[] {
	const p = pixel(centre, zoom);
	const [cx, cy] = [Math.floor(p.x), Math.floor(p.y)];
	const [demiL, demiH] = [taille.largeur / 2, taille.hauteur / 2];
	const [x0, x1] = [Math.floor((cx - demiL) / TAILLE_TUILE), Math.ceil((cx + demiL) / TAILLE_TUILE) - 1];
	const [y0, y1] = [Math.floor((cy - demiH) / TAILLE_TUILE), Math.ceil((cy + demiH) / TAILLE_TUILE) - 1];
	const tuiles: string[] = [];
	for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) tuiles.push(`${zoom}/${x}/${y}`);
	return tuiles;
}

/** Bounds of a frame of *taille* centred on *centre* at *zoom*: what the map would show at its opening. */
export function bornesDuCadre(centre: { lon: number; lat: number }, zoom: number, taille: TailleCadre): BornesVue {
	const p = pixel(centre, zoom);
	const nordOuest = point(p.x - taille.largeur / 2, p.y - taille.hauteur / 2, zoom);
	const sudEst = point(p.x + taille.largeur / 2, p.y + taille.hauteur / 2, zoom);
	return { ouest: nordOuest.lon, sud: sudEst.lat, est: sudEst.lon, nord: nordOuest.lat };
}

/** The parcels of the view, in the given order. */
export function parcellesDeLaVue(parcelles: Iterable<ParcelleCarte>, vue: BornesVue): ParcelleCarte[] {
	return [...parcelles].filter((p) => dansLaVue(p, vue));
}

/**
 * Parcels with at least one sale over the served period. Null as soon as one parcel has its sales not
 * counted (`ventes` null): a count of the others would read as fewer sold parcels.
 */
export function ventesComptees(parcelles: readonly ParcelleCarte[]): number | null {
	let vendues = 0;
	for (const p of parcelles) {
		if (p.properties.ventes === null) return null;
		if (p.properties.ventes > 0) vendues += 1;
	}
	return vendues;
}
