import { describe, it, expect } from 'vitest';
import { safeRedirect } from './safe-redirect';

describe('safeRedirect()', () => {
	it('accepts a plain internal path', () => {
		expect(safeRedirect('/bien/46.8,-1.88')).toBe('/bien/46.8,-1.88');
	});

	it('accepts an internal path with a benign query string', () => {
		expect(safeRedirect('/communes?departement=85&page=2')).toBe(
			'/communes?departement=85&page=2'
		);
	});

	it('rejects an absolute external URL', () => {
		expect(safeRedirect('https://evil.com')).toBe('/mon-espace');
	});

	it('rejects a protocol-relative URL', () => {
		expect(safeRedirect('//evil.com')).toBe('/mon-espace');
	});

	it('rejects a javascript: scheme', () => {
		expect(safeRedirect('javascript:alert(1)')).toBe('/mon-espace');
	});

	it('rejects an encoded-slash smuggling attempt', () => {
		expect(safeRedirect('/bien/x?y=%2F%2Fevil')).toBe('/mon-espace');
	});

	it('rejects an empty string', () => {
		expect(safeRedirect('')).toBe('/mon-espace');
	});

	it('rejects null and undefined', () => {
		expect(safeRedirect(null)).toBe('/mon-espace');
		expect(safeRedirect(undefined)).toBe('/mon-espace');
	});

	it('rejects a backslash trick', () => {
		expect(safeRedirect('/\\evil.com')).toBe('/mon-espace');
	});

	it('honours a custom fallback', () => {
		expect(safeRedirect('https://evil.com', '/login')).toBe('/login');
	});
});
