// The preview of a departement page (SEO audit of 2026-11-14): figures served, its own image; an outage,
// the generic one.
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://site.invalid/departement/44'), state: {} })
}));

import { OG_IMAGE_URL, SITE_URL } from '$lib/config';
import { DEPARTEMENT_44, DEPARTEMENTS_LISTE } from '$lib/data/__fixtures__/departement';
import type { DepartementPageData } from './+page';
import Page from './+page.svelte';

const meta = (selecteur: string) => document.head.querySelector(selecteur)?.getAttribute('content') ?? null;
const SOURCES = [{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/dvf' }];

afterEach(() => {
	document.head.innerHTML = '';
});

describe('/departement/[code] — og:image', () => {
	it('names its own image with its alternative text', () => {
		const data: DepartementPageData = { etat: 'synthese', code: '44', synthese: DEPARTEMENT_44, sources: SOURCES, departements: DEPARTEMENTS_LISTE, fiches: null };
		render(Page, { props: { data } });
		expect(meta('meta[property="og:image"]')).toBe(`${SITE_URL}/og/departement/44.png`);
		expect(meta('meta[name="twitter:image"]')).toBe(`${SITE_URL}/og/departement/44.png`);
		expect(meta('meta[property="og:image:alt"]')).toMatch(/^Loire-Atlantique — Département 44 · Pays de la Loire\. Appartements, prix médian : /);
	});

	it('an outage keeps the generic image', () => {
		const data: DepartementPageData = { etat: 'panne', code: '44', nom: 'Loire-Atlantique', detail: { statut: 503, titre: null, message: null }, retryAfter: null };
		render(Page, { props: { data } });
		expect(meta('meta[property="og:image"]')).toBe(OG_IMAGE_URL);
		expect(document.head.querySelector('meta[property="og:image:alt"]')).toBeNull();
	});
});
