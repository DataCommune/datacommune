import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
	document.head.querySelectorAll('[data-leaflet]').forEach((el) => el.remove());
	delete (window as unknown as { L?: unknown }).L;
	vi.resetModules();
});

/** The loader of a fresh module: its memory of a load lives for the life of the page. */
async function chargeur() {
	return (await import('./chargeur-carte')).chargerLeaflet;
}

const script = () => document.head.querySelector<HTMLScriptElement>('script[data-leaflet]');
const feuille = () => document.head.querySelector<HTMLLinkElement>('link[data-leaflet]');

describe('chargerLeaflet (04.10-06, T-39)', () => {
	it('adds one classic script and one style sheet of the site origin, and resolves to window.L', async () => {
		const chargerLeaflet = await chargeur();
		const lu = chargerLeaflet();
		expect(script()?.type).toBe('');
		expect(script()?.src).toMatch(/^http:\/\/localhost(:\d+)?\/.*leaflet.*\.js/);
		expect(feuille()?.rel).toBe('stylesheet');
		const L = { map: () => null };
		(window as unknown as { L: unknown }).L = L;
		script()!.dispatchEvent(new Event('load'));
		feuille()!.dispatchEvent(new Event('load'));
		await expect(lu).resolves.toBe(L);
		// Asked again (the dialog opened a second time): nothing is added, the same Leaflet comes back.
		await expect(chargerLeaflet()).resolves.toBe(L);
		expect(document.head.querySelectorAll('[data-leaflet]')).toHaveLength(2);
	});

	it('a failed script is removed and asked again on the next call: a new element, a new fetch', async () => {
		const chargerLeaflet = await chargeur();
		const premier = chargerLeaflet();
		const echoue = script()!;
		feuille()!.dispatchEvent(new Event('load'));
		echoue.dispatchEvent(new Event('error'));
		await expect(premier).rejects.toThrow();
		expect(echoue.isConnected).toBe(false);

		const second = chargerLeaflet();
		expect(script()).not.toBe(echoue);
		// The style sheet already read is kept, not asked twice.
		expect(document.head.querySelectorAll('link[data-leaflet]')).toHaveLength(1);
		const L = { map: () => null };
		(window as unknown as { L: unknown }).L = L;
		script()!.dispatchEvent(new Event('load'));
		await expect(second).resolves.toBe(L);
	});

	it('a script read without window.L is a failure, never a map mounted on nothing', async () => {
		const chargerLeaflet = await chargeur();
		const lu = chargerLeaflet();
		script()!.dispatchEvent(new Event('load'));
		feuille()!.dispatchEvent(new Event('load'));
		await expect(lu).rejects.toThrow();
	});
});
