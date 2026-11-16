// French spacing of the served sentences (04.8-03, D-37; UI-SPEC v4 § 7.5).
import { describe, expect, it } from 'vitest';
import { typographieFr } from './typographie';

const NBSP = ' ';
const NNBSP = ' ';

describe('typographieFr (D-37)', () => {
	it('a real served sentence (altitude.py:50) gets U+00A0 before « : » and between 21 and m', () => {
		expect(typographieFr("Altitude du sol au point de l'adresse : 21 m.")).toBe(`Altitude du sol au point de l'adresse${NBSP}: 21${NBSP}m.`);
	});

	it('U+202F before « ; », « ! », « ? » and inside « »', () => {
		expect(typographieFr('Zone A ; zone B ! Pourquoi ? Voir « PPRI ».')).toBe(
			`Zone A${NNBSP}; zone B${NNBSP}! Pourquoi${NNBSP}? Voir «${NNBSP}PPRI${NNBSP}».`
		);
	});

	it('U+00A0 between a number and its unit', () => {
		expect(typographieFr('À 500 m, 1,2 km, 54 m², 120 € et 3 % ; 12 mois, 2 ventes, 4 diagnostics.')).toBe(
			`À 500${NBSP}m, 1,2${NBSP}km, 54${NBSP}m², 120${NBSP}€ et 3${NBSP}%${NNBSP}; 12${NBSP}mois, 2${NBSP}ventes, 4${NBSP}diagnostics.`
		);
		// A word that starts like a unit is not a unit.
		expect(typographieFr('5 maisons et 2 mairies')).toBe('5 maisons et 2 mairies');
	});

	it('leaves a postal code, a BAN identifier, a URL, a time and a bare number untouched', () => {
		for (const texte of ['44000', '44109_zz04_00004', 'https://www.georisques.gouv.fr/?q=a:b', '12:30', '1965', 'Nantes:Est']) {
			expect(typographieFr(texte)).toBe(texte);
		}
	});

	it('leaves a correct sentence as it is, and is idempotent', () => {
		const correcte = `Zone${NBSP}: 21${NBSP}m${NNBSP}; «${NNBSP}x${NNBSP}»${NNBSP}?`;
		expect(typographieFr(correcte)).toBe(correcte);
		const brute = "Altitude : 21 m ; « PPRI » ? 3 % !";
		expect(typographieFr(typographieFr(brute))).toBe(typographieFr(brute));
	});
});
