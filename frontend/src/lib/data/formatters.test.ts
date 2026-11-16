import { describe, expect, it } from 'vitest';
import { formatField, formatNullableField } from './formatters';

describe('additive presentation qualification from raw values', () => {
	it.each(['revenus.revenu_median', 'bien.dpe.surface_habitable'])('does not qualify an implausible zero as measured: %s', fieldId => {
		const row = formatField(fieldId, 'Synthetic zero', { state: 'value', value: 0 });
		expect(row.state).toBe('value');
		expect(row.value).toMatch(/^0 /);
		expect(row.presentation).toEqual({ state: 'value', measuredZero: false });
	});
	it('marks numeric zero before formatting, preserving display and state', () => {
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 0 })).toMatchObject({
			value: '0 %', state: 'value', presentation: { state: 'value', measuredZero: true }
		});
	});
	it('does not treat a rounded zero or textual identifier as a measured zero', () => {
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: 0.0001 })).toMatchObject({
			value: '0 %', presentation: { state: 'value', measuredZero: false }
		});
		expect(formatField('commune.code_insee', 'Code', { state: 'value', value: '0' }).presentation).toEqual({ state: 'value', measuredZero: false });
	});
	it('recognizes a raw numeric API string without parsing the display', () => {
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: '0.00' }).presentation).toEqual({ state: 'value', measuredZero: true });
	});
	it('carries absent and degraded states without raw values or guessed reasons', () => {
		expect(formatNullableField('vacance.taux_vacance', 'Vacance', null).presentation).toEqual({ state: 'out_of_coverage' });
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'value', value: NaN }).presentation).toEqual({ state: 'temporary_error' });
		expect(formatField('vacance.taux_vacance', 'Vacance', { state: 'not_reported' }).presentation).toEqual({ state: 'not_reported' });
	});
});
