// The static referential of departements names what the API serves by code (hosting, search,
// comparison). It must hold every code the backend knows (app/etl/base.py DEPARTEMENTS: 01..19,
// 2A, 2B, 21..95, 971..976), or a served code is shown as « Département {code} ».
import { describe, expect, it } from 'vitest';
import { DEPARTEMENTS } from './departements';

const CODES_BACKEND = [
	...Array.from({ length: 19 }, (_, i) => String(i + 1).padStart(2, '0')),
	'2A',
	'2B',
	...Array.from({ length: 75 }, (_, i) => String(i + 21)),
	...Array.from({ length: 6 }, (_, i) => String(i + 971))
];

describe('DEPARTEMENTS — static referential', () => {
	it('holds the 102 codes of the backend, each once and named', () => {
		const codes = DEPARTEMENTS.map((d) => d.code);
		expect(new Set(codes).size).toBe(codes.length);
		expect([...codes].sort()).toEqual([...CODES_BACKEND].sort());
		for (const d of DEPARTEMENTS) expect(d.nom.trim()).not.toBe('');
	});

	it('names Saint-Pierre-et-Miquelon (975), between La Réunion and Mayotte', () => {
		const codes = DEPARTEMENTS.map((d) => d.code);
		expect(DEPARTEMENTS.find((d) => d.code === '975')?.nom).toBe('Saint-Pierre-et-Miquelon');
		expect(codes.indexOf('975')).toBe(codes.indexOf('974') + 1);
		expect(codes.indexOf('976')).toBe(codes.indexOf('975') + 1);
	});
});
