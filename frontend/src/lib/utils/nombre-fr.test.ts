// French numbers and distances (04.8-03, D-36), moved out of `adresse-rapport.ts` which re-exports them.
import { describe, expect, it } from 'vitest';
import { distanceFr, nombreFr } from './nombre-fr';
import { distanceFr as distanceReexportee, nombreFr as nombreReexporte } from '$lib/data/adresse-rapport';

const NBSP = '\u00a0';

describe('nombreFr et distanceFr (04.8-03)', () => {
	it('writes French numbers, never padded', () => {
		expect(nombreFr(98.5)).toBe('98,5');
		expect(nombreFr(1, 1)).toBe('1,0');
	});

	it('writes a distance in metres, then in kilometres, a whole kilometre without « ,0 »', () => {
		expect(distanceFr(500)).toBe(`500${NBSP}m`);
		expect(distanceFr(1000)).toBe(`1${NBSP}km`);
		expect(distanceFr(1200)).toBe(`1,2${NBSP}km`);
		expect(distanceFr(10000)).toBe(`10${NBSP}km`);
	});

	it('adresse-rapport re-exports the same functions (its importers are unchanged)', () => {
		expect(distanceReexportee).toBe(distanceFr);
		expect(nombreReexporte).toBe(nombreFr);
	});
});
