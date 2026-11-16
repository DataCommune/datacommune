import { describe, it, expect } from 'vitest';
import { slugifyAdresse, deslugifyAdresse } from './slug';

describe('slugifyAdresse()', () => {
	it('produces a lowercase, accent-free, "-"-separated URL token (D-04)', () => {
		expect(slugifyAdresse('12 Rue de la Paix, 44000 Nantes')).toBe(
			'12-rue-de-la-paix-44000-nantes'
		);
	});

	it('translitterates accented French characters to ASCII', () => {
		const slug = slugifyAdresse('Église, 44000 Nantes');
		expect(slug).not.toMatch(/[ÉéÈè]/i);
		expect(slug).toMatch(/^[a-z0-9-]*$/);
	});

	it('never contains "/", spaces, commas or uppercase letters', () => {
		const slug = slugifyAdresse('12 Rue de la Paix, 44000 Nantes');
		expect(slug).toMatch(/^[a-z0-9-]*$/);
	});

	it('collapses multiple separators and trims edge "-"', () => {
		expect(slugifyAdresse('  12,   Rue Foo -- Bar  ')).not.toMatch(/^-|-$|--/);
	});
});

describe('deslugifyAdresse()', () => {
	it('reconstructs a non-empty, re-typeable text (round-trip, fuzzy geocoder — no strict equality required)', () => {
		const slug = slugifyAdresse('12 Rue de la Paix, 44000 Nantes');
		const back = deslugifyAdresse(slug);
		expect(back.length).toBeGreaterThan(0);
		expect(back).not.toContain('-');
	});

	it('returns an empty string for an empty slug', () => {
		expect(deslugifyAdresse('')).toBe('');
	});

	it('returns an empty string for a slug made only of separators', () => {
		expect(deslugifyAdresse('---')).toBe('');
	});
});
