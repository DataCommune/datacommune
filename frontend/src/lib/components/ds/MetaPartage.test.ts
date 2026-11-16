// Sharing metadata of a public page (socle of 2026-11-09, T1): one component writes the title, the
// description, the canonical and the Open Graph and Twitter tags the reference pages write by hand
// (`commune/[code]/+page.svelte`), so a shared link always has its card.
import { render } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { OG_IMAGE_URL, SITE_URL } from '$lib/config';
import MetaPartage from './MetaPartage.svelte';

const meta = (selecteur: string) => document.head.querySelector(selecteur)?.getAttribute('content') ?? null;
const propriete = (nom: string) => meta(`meta[property="${nom}"]`);
const nommee = (nom: string) => meta(`meta[name="${nom}"]`);
const canonique = () => document.head.querySelector('link[rel="canonical"]')?.getAttribute('href') ?? null;

afterEach(() => {
	document.head.innerHTML = '';
});

describe('MetaPartage', () => {
	it('writes the title, the description, the canonical and the whole card of the reference pages', () => {
		render(MetaPartage, { titre: 'Challans (85300) — DataCommune', description: 'Prix, logement et cadre de vie.', chemin: '/commune/85047' });
		const url = `${SITE_URL}/commune/85047`;
		expect(document.title).toBe('Challans (85300) — DataCommune');
		expect(nommee('description')).toBe('Prix, logement et cadre de vie.');
		expect(canonique()).toBe(url);
		expect({
			type: propriete('og:type'),
			site: propriete('og:site_name'),
			titre: propriete('og:title'),
			description: propriete('og:description'),
			url: propriete('og:url'),
			image: propriete('og:image'),
			locale: propriete('og:locale')
		}).toEqual({
			type: 'website',
			site: 'DataCommune',
			titre: 'Challans (85300) — DataCommune',
			description: 'Prix, logement et cadre de vie.',
			url,
			image: OG_IMAGE_URL,
			locale: 'fr_FR'
		});
		expect({
			carte: nommee('twitter:card'),
			titre: nommee('twitter:title'),
			description: nommee('twitter:description'),
			image: nommee('twitter:image')
		}).toEqual({
			carte: 'summary_large_image',
			titre: 'Challans (85300) — DataCommune',
			description: 'Prix, logement et cadre de vie.',
			image: OG_IMAGE_URL
		});
		// Nothing asked, nothing written: a page is indexed unless it says otherwise.
		expect(nommee('robots')).toBeNull();
	});

	it('a page kept out of the index says so and has no canonical, its card keeps its address', () => {
		render(MetaPartage, {
			titre: '12 rue des Lilas — DataCommune',
			description: 'Fiche adresse.',
			chemin: '/adresse/35238_1234_00012',
			canonique: false,
			robots: 'noindex, nofollow'
		});
		expect(nommee('robots')).toBe('noindex, nofollow');
		expect(canonique()).toBeNull();
		expect(propriete('og:url')).toBe(`${SITE_URL}/adresse/35238_1234_00012`);
		expect(propriete('og:image')).toBe(OG_IMAGE_URL);
	});

	it('without an address the card has no og:url and the page no canonical', () => {
		render(MetaPartage, { titre: 'Comparer — DataCommune', description: 'Comparer des communes.' });
		expect(canonique()).toBeNull();
		expect(propriete('og:url')).toBeNull();
		expect(propriete('og:title')).toBe('Comparer — DataCommune');
		expect(nommee('twitter:card')).toBe('summary_large_image');
	});

	it('writes each tag once', () => {
		render(MetaPartage, { titre: 'T', description: 'D', chemin: '/', robots: 'noindex, follow' });
		for (const selecteur of [
			'meta[name="description"]',
			'link[rel="canonical"]',
			'meta[name="robots"]',
			'meta[property="og:image"]',
			'meta[name="twitter:image"]'
		]) {
			expect(document.head.querySelectorAll(selecteur), selecteur).toHaveLength(1);
		}
		expect(canonique()).toBe(`${SITE_URL}/`);
	});
});
