// French numbers and distances (04.8-03, D-36): moved out of `adresse-rapport.ts`, which re-exports
// them, so the scale table and the components of the fiche share one frozen interface.

const NBSP = String.fromCharCode(0xa0); // no-break space

/** French number; `decimales` fixes the digits, otherwise up to two and never padded (98,5, not 98,50). */
export function nombreFr(n: number, decimales?: number): string {
	return new Intl.NumberFormat('fr-FR', { minimumFractionDigits: decimales ?? 0, maximumFractionDigits: decimales ?? 2 }).format(n);
}

/** « 500 m », « 1 km », « 1,2 km » : a whole number of kilometres is written without « ,0 ». */
export const distanceFr = (m: number): string =>
	m >= 1000 ? `${nombreFr(m / 1000, m % 1000 === 0 ? 0 : 1)}${NBSP}km` : `${Math.round(m)}${NBSP}m`;
