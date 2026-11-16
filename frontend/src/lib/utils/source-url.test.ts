import { describe, expect, it } from 'vitest';
import { urlHttpsSeulement } from './source-url';

describe('shared publication URL guard', () => {
	it('preserves an HTTPS publication URL', () => {
		expect(urlHttpsSeulement('https://example.org/source?a=1')).toBe('https://example.org/source?a=1');
	});
	it.each(['javascript:alert(1)', 'data:text/html,test', 'http://example.org', 'invalid', '//example.org', '', null, undefined])('refuses %s', value => {
		expect(urlHttpsSeulement(value)).toBeNull();
	});
});
