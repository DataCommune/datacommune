// The preview of a commune fiche (SEO audit of 2026-11-14): a fiche with served figures names its own
// image, with the alternative text of the card it draws; any other state keeps the generic image.
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://site.invalid/commune/85191'), state: {} })
}));

import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { OG_IMAGE_URL, SITE_URL } from '$lib/config';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '$lib/data/__fixtures__/commune-85191-20261012';
import { carteCommune } from '$lib/seo/og-carte';
import type { FicheCommuneData } from './+page';
import Page from './+page.svelte';

const meta = (selecteur: string) => document.head.querySelector(selecteur)?.getAttribute('content') ?? null;

afterEach(() => {
	document.head.innerHTML = '';
});

describe('/commune/[code] — og:image', () => {
	it('a fiche names its own image, its size and the alternative text of its card', () => {
		const data = {
			etat: 'fiche',
			code: '85191',
			parent: null,
			synthese: COMMUNE_85191_OBSERVED as unknown as CommuneSynthese,
			sources: COMMUNE_85191_SOURCES as unknown as SourceAttribution[],
			consulteLe: '14 novembre 2026'
		} as FicheCommuneData;
		render(Page, { props: { data } });
		expect(meta('meta[property="og:image"]')).toBe(`${SITE_URL}/og/commune/85191.png`);
		expect(meta('meta[name="twitter:image"]')).toBe(`${SITE_URL}/og/commune/85191.png`);
		expect(meta('meta[property="og:image:width"]')).toBe('1200');
		expect(meta('meta[property="og:image:height"]')).toBe('630');
		expect(meta('meta[property="og:image:alt"]')).toBe(carteCommune(data as Extract<FicheCommuneData, { etat: 'fiche' }>)!.alt);
		expect(document.head.querySelectorAll('meta[property="og:image"]')).toHaveLength(1);
	});

	it('a commune out of coverage keeps the generic image, without an alternative text', () => {
		const data = { etat: 'hors-couverture', code: '75056', identite: { nom: 'Paris', departement: '75', codePostal: '75001' }, motif: null } as FicheCommuneData;
		render(Page, { props: { data } });
		expect(meta('meta[property="og:image"]')).toBe(OG_IMAGE_URL);
		expect(meta('meta[name="twitter:image"]')).toBe(OG_IMAGE_URL);
		expect(document.head.querySelector('meta[property="og:image:alt"]')).toBeNull();
	});
});
