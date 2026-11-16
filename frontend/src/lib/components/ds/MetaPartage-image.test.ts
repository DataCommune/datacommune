// The preview of a page (SEO audit of 2026-11-14): a page that has its own image names it, with its size
// and its alternative text, in the Open Graph and Twitter tags; without it, the generic image stands.
import { render } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { OG_IMAGE_URL } from '$lib/config';
import MetaPartage from './MetaPartage.svelte';

const meta = (selecteur: string) => document.head.querySelector(selecteur)?.getAttribute('content') ?? null;

afterEach(() => {
	document.head.innerHTML = '';
});

describe('MetaPartage — image', () => {
	it('names the image of the page, its size and its alternative text', () => {
		render(MetaPartage, {
			titre: 'T',
			description: 'D',
			chemin: '/departement/44',
			image: { url: 'https://datacommune.fr/og/departement/44.png', alt: 'Loire-Atlantique — Département 44.' }
		});
		expect(meta('meta[property="og:image"]')).toBe('https://datacommune.fr/og/departement/44.png');
		expect(meta('meta[property="og:image:width"]')).toBe('1200');
		expect(meta('meta[property="og:image:height"]')).toBe('630');
		expect(meta('meta[property="og:image:alt"]')).toBe('Loire-Atlantique — Département 44.');
		expect(meta('meta[name="twitter:image"]')).toBe('https://datacommune.fr/og/departement/44.png');
	});

	it('without an image of its own, the generic image, and no alternative text for it', () => {
		render(MetaPartage, { titre: 'T', description: 'D', chemin: '/departements' });
		expect(meta('meta[property="og:image"]')).toBe(OG_IMAGE_URL);
		expect(meta('meta[name="twitter:image"]')).toBe(OG_IMAGE_URL);
		expect(meta('meta[property="og:image:width"]')).toBe('1200');
		expect(document.head.querySelector('meta[property="og:image:alt"]')).toBeNull();
	});
});
