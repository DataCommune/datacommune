import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import SiteFooter from './SiteFooter.svelte';

const etat = vi.hoisted(() => ({ route: null as string | null, carte: null as unknown }));
vi.mock('$app/stores', async () => {
	const { readable } = await import('svelte/store');
	return {
		page: {
			subscribe: (run: (value: unknown) => void) =>
				readable({ route: { id: etat.route }, data: { issue: { kind: 'ok', rapport: { carte: etat.carte } } }, url: new URL('http://localhost/') }).subscribe(run)
		}
	};
});

// 04.8-03 (UI-SPEC v4 § 2.5, § 3.11): the licence of the map background, on the address fiche that shows
// its served map only, the one line of the footer printed.
describe('SiteFooter — licence IGN de la carte (04.8-03)', () => {
	it('is said on an address fiche with its served map, printed', () => {
		etat.route = '/(marketing)/adresse/[id]';
		etat.carte = { plan: '/carte/x/plan.png' };
		const { container } = render(SiteFooter);
		expect(container.querySelector('.site-footer-ign')?.textContent).toBe('Fonds de carte : Plan IGN, © IGN, Licence Ouverte 2.0.');
		expect(container.querySelector('footer')?.classList.contains('print:hidden')).toBe(false);
		expect(container.querySelector('nav')?.classList.contains('print:hidden')).toBe(true);
	});

	it('is absent elsewhere and without a served map, the footer not printed', () => {
		for (const [route, carte] of [
			['/(marketing)/commune/[code]', { plan: '/carte/x/plan.png' }],
			['/(marketing)/adresse/[id]', null]
		] as const) {
			etat.route = route;
			etat.carte = carte;
			const { container, unmount } = render(SiteFooter);
			expect(container.querySelector('.site-footer-ign')).toBeNull();
			expect(container.querySelector('footer')?.classList.contains('print:hidden')).toBe(true);
			unmount();
		}
		etat.route = null;
	});
});

describe('SiteFooter (D-50)', () => {
	it('lists the nine footer destinations in the contract order (« Lire une fiche » second, 04.8-06, « Code source » after « Développeurs », 05-07 lot 6), then the contact link', () => {
		const { getAllByRole } = render(SiteFooter);
		const links = getAllByRole('link').map((a) => [a.textContent?.trim(), a.getAttribute('href')]);
		expect(links).toEqual([
			['Données & méthode', '/datasets'],
			['Lire une fiche', '/datasets#lire'],
			['Couverture et limites', '/couverture'],
			['Ce que nous ne montrons pas', '/ce-que-nous-ne-montrons-pas'],
			['Développeurs', '/developpeurs'],
			['Code source', 'https://github.com/DataCommune/datacommune'],
			['Mentions légales', '/mentions-legales'],
			['Confidentialité', '/confidentialite'],
			['CGU', '/cgu'],
			// Socle (2026-11-09, I9): a way to report an error, from every page. Wording to be confirmed.
			['Signaler une erreur', 'mailto:signalement@datacommune.fr']
		]);
	});

	// 04.8-08 (D-34, UI-SPEC § 3.11): the mockup's sentence — each value cites its source, its date and
	// its scale — replaces « périodes et limites », and never says the code will be published (A-11).
	it('presents open public data, each value with its source, date and scale, without implementation jargon', () => {
		const { container } = render(SiteFooter);
		const note = (container.querySelector('.site-footer-note')?.textContent ?? '').replace(/\s+/g, ' ').trim();
		expect(note).toBe('DataCommune rassemble des données publiques ouvertes. Chaque valeur cite sa source, sa date et l’échelle à laquelle elle vaut.');
		expect(container.querySelector('.site-footer-note strong')?.textContent).toBe('DataCommune');
		expect(note).not.toMatch(/code sera publié/i);
		expect(note).not.toMatch(/\b(?:API|instance)\b/i);
	});

	it('is static: no request and no figure', () => {
		const fetchSpy = vi.spyOn(globalThis, 'fetch');
		const { container } = render(SiteFooter);
		expect(fetchSpy).not.toHaveBeenCalled();
		expect(container.textContent ?? '').not.toMatch(/\d/);
		fetchSpy.mockRestore();
	});
});
