import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import NatureNav from './NatureNav.svelte';

// Nature switch of `/comparer` (UI-SPEC « /comparer », 04-10): three links of 44 px, the active one
// with `aria-current="page"`. T5 (proposed on 2026-11-10, to be reread by Ianis): an empty nature shows its
// name alone, its link kept; « (0) » three times was the first figure of the empty comparator.

const LIENS = { commune: '/comparer?communes=44109,85191', adresse: '/comparer?adresses=', dpe: '/comparer?dpe=2385E0123456A' };

describe('NatureNav', () => {
	it('names the three natures with their counts, in a named navigation', () => {
		const { getByRole } = render(NatureNav, { active: 'dpe', comptes: { commune: 2, adresse: 0, dpe: 1 }, liens: LIENS });
		const nav = getByRole('navigation', { name: 'Nature des objets comparés' });
		const liens = [...nav.querySelectorAll('a')];
		expect(liens.map((a) => a.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Communes (2)',
			'Adresses',
			'Documents DPE (1)'
		]);
		expect(liens.map((a) => a.getAttribute('href'))).toEqual(Object.values(LIENS));
	});

	it('marks the active nature only, an empty nature keeping its link', () => {
		const { getByRole } = render(NatureNav, { active: 'dpe', comptes: { commune: 2, adresse: 0, dpe: 1 }, liens: LIENS });
		expect(getByRole('link', { name: 'Documents DPE (1)' })).toHaveAttribute('aria-current', 'page');
		expect(getByRole('link', { name: 'Communes (2)' })).not.toHaveAttribute('aria-current');
		expect(getByRole('link', { name: 'Adresses' })).toHaveAttribute('href', '/comparer?adresses=');
	});

	it('gives each link 44 px, uses the public accent and keeps the legacy ink default', async () => {
		const source = (await import('./NatureNav.svelte?raw')).default;
		expect(source).toMatch(/\.nature-nav-lien \{[^}]*min-height: 44px;/);
		expect(source).toMatch(/\.nature-nav-lien\[aria-current='page'\] \{[^}]*border-bottom-color: var\(--color-ink\);/);
		expect(source).toMatch(/:global\(\.public-site\) \.nature-nav-lien\[aria-current='page'\] \{[^}]*border-bottom-color: var\(--color-accent\);/);
	});
});
