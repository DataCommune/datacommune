// The internal marker of a page whose data source failed (04.7-10, decision 11): a load posts it,
// `hooks.server.ts` turns it into a 503 with `Retry-After` and removes it.
import { describe, expect, it, vi } from 'vitest';
import {
	DEFAULT_RETRY_AFTER_SECONDS,
	markOutage,
	MAX_RETRY_AFTER_SECONDS,
	OUTAGE_RETRY_HEADER,
	PAGE_STATE_HEADER,
	PAGE_STATE_OUTAGE
} from './etat-page';

describe('markOutage', () => {
	it('posts the outage marker and the served Retry-After, as strings', () => {
		const setHeaders = vi.fn();
		markOutage(setHeaders, 120);
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ [PAGE_STATE_HEADER]: PAGE_STATE_OUTAGE, [OUTAGE_RETRY_HEADER]: '120' });
	});

	it('names the header x-dc-etat-page and the state panne', () => {
		expect(PAGE_STATE_HEADER).toBe('x-dc-etat-page');
		expect(PAGE_STATE_OUTAGE).toBe('panne');
	});

	it('posts the marker alone when no usable Retry-After was served', () => {
		for (const retryAfter of [null, undefined, 0, -3, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
			const setHeaders = vi.fn();
			markOutage(setHeaders, retryAfter);
			expect(setHeaders, String(retryAfter)).toHaveBeenCalledWith({ [PAGE_STATE_HEADER]: PAGE_STATE_OUTAGE });
		}
	});

	it('does nothing without a setHeaders (client navigation, tests, JSON export)', () => {
		expect(() => markOutage(undefined, 30)).not.toThrow();
	});

	it('keeps the default and the ceiling of Retry-After sensible', () => {
		expect(DEFAULT_RETRY_AFTER_SECONDS).toBe(60);
		expect(MAX_RETRY_AFTER_SECONDS).toBe(86_400);
	});
});
