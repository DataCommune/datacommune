import { describe, it, expect, vi, afterEach } from 'vitest';
import { dateConsultationFr } from './date-consultation';

describe('dateConsultationFr()', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('renders JJ/MM/AAAA (D-12)', () => {
		expect(dateConsultationFr(new Date('2026-07-29T10:00:00Z'))).toBe('29/07/2026');
	});

	it('pads the day and the month to two digits, the year to four', () => {
		expect(dateConsultationFr(new Date('2026-01-05T10:30:00Z'))).toBe('05/01/2026');
	});

	it('reads the day in Europe/Paris, wherever the code runs (4.9 lot 1, C8.6)', () => {
		// 00:30 in Paris is still the day before in UTC: reading UTC showed the wrong day for one or
		// two hours every night on a server-rendered page.
		expect(dateConsultationFr(new Date('2026-01-05T23:30:00Z'))).toBe('06/01/2026');
		expect(dateConsultationFr(new Date('2026-07-05T22:30:00Z'))).toBe('06/07/2026');
		// Just before midnight in Paris stays on the same day.
		expect(dateConsultationFr(new Date('2026-07-05T21:59:00Z'))).toBe('05/07/2026');
	});

	it('gives the same day whatever the zone the code runs in, server or browser', () => {
		const zone = process.env.TZ;
		try {
			for (const fuseau of ['UTC', 'Europe/Paris', 'America/New_York', 'Asia/Tokyo']) {
				process.env.TZ = fuseau;
				expect(dateConsultationFr(new Date('2026-01-05T23:30:00Z')), fuseau).toBe('06/01/2026');
				expect(dateConsultationFr(new Date('2026-07-05T21:59:00Z')), fuseau).toBe('05/07/2026');
			}
		} finally {
			process.env.TZ = zone;
		}
	});

	it('falls back on the current instant when called without argument', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2026-03-08T06:00:00Z'));
		expect(dateConsultationFr()).toBe('08/03/2026');
	});

	it('fails loudly on an invalid date instead of rendering "Invalid Date" or "NaN/NaN/NaN"', () => {
		expect(() => dateConsultationFr(new Date('pas-une-date'))).toThrow();
		expect(() => dateConsultationFr(new Date(Number.NaN))).toThrow();
	});

	it('never renders NaN nor the string "undefined" on a valid date', () => {
		const rendu = dateConsultationFr(new Date('2026-12-31T22:00:00Z'));
		expect(rendu).toBe('31/12/2026');
		expect(rendu).not.toContain('NaN');
		expect(rendu).not.toContain('undefined');
	});
});
