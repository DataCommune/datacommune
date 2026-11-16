import { describe, it, expect } from 'vitest';
import { formatPriceBucket } from './dvf-minimisation';
import { displayUnit, SemanticContractError } from './formatters';

/** Collapse every space flavour (U+0020, NBSP, NNBSP — all in JS \s) so assertions survive ICU grouping. */
function compact(s: string): string {
	return s.replace(/\s/g, '');
}

describe('formatPriceBucket() — D-03 bucket carries the unit of ITS field (CR-01)', () => {
	it('prix_m2 bracket ends with the €/m² unit read from the contract', () => {
		const out = formatPriceBucket('bien.dvf.prix_m2', 2154, 500);
		expect(compact(out)).toBe('2000–2500€/m²');
		// Structural tie to the ℹ️ panel: the suffix IS displayUnit's output, not a lookalike.
		expect(out.endsWith(` ${displayUnit('bien.dvf.prix_m2')}`)).toBe(true);
	});

	it('valeur_fonciere bracket keeps its € unit and never says €/m²', () => {
		const out = formatPriceBucket('bien.dvf.valeur_fonciere', 168000, 50_000);
		expect(compact(out)).toBe('150000–200000€');
		expect(out).not.toContain('€/m²');
		expect(out.endsWith(` ${displayUnit('bien.dvf.valeur_fonciere')}`)).toBe(true);
	});

	it('the low bound is floored to a multiple of the step (never reconstructs the exact value)', () => {
		expect(compact(formatPriceBucket('bien.dvf.valeur_fonciere', 199_999, 50_000))).toBe(
			'150000–200000€'
		);
		expect(compact(formatPriceBucket('bien.dvf.prix_m2', 2500, 500))).toBe('2500–3000€/m²');
	});

	it('an unregistered field_id fails loud (T-43-07), never a silent unitless bracket', () => {
		expect(() => formatPriceBucket('this.field.does.not.exist', 1000, 500)).toThrow(
			SemanticContractError
		);
	});
});
