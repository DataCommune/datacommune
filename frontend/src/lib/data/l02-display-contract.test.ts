import { describe, expect, it } from 'vitest';
import { displayUnit, formatNullableField } from './formatters';

describe('L02 — declared income display contract', () => {
	it('keeps the annual consumption-unit denominator in rows and explanations', () => {
		expect(displayUnit('revenus.revenu_median')).toBe('€/UC/an');
		expect(formatNullableField('revenus.revenu_median', 'Revenu déclaré médian', 24380).value)
			.toMatch(/24\s380 €\/UC\/an/);
	});
});
