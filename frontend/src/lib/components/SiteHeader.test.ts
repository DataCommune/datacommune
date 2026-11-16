import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import SiteHeader from './SiteHeader.svelte';

const state = vi.hoisted(() => ({ accountsEnabled: true, pathname: '/' }));

vi.mock('$app/stores', async () => {
	const { readable } = await import('svelte/store');
	return { page: { subscribe: (run: (value: { url: URL }) => void) => readable({ url: new URL(`http://localhost${state.pathname}`) }).subscribe(run) } };
});
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => undefined), afterNavigate: vi.fn() }));
// `ACCOUNTS_ENABLED` is a build-time constant; the mock reads it live so one module graph serves both states.
vi.mock('$lib/config', async (importOriginal) => {
	const mod = { ...(await importOriginal<typeof import('$lib/config')>()) };
	Object.defineProperty(mod, 'ACCOUNTS_ENABLED', { get: () => state.accountsEnabled, enumerable: true });
	return mod;
});

describe('SiteHeader account switch', () => {
	it('renders the account zone while accounts are enabled', () => {
		state.accountsEnabled = true;
		const { container } = render(SiteHeader);
		expect(container.querySelector('.site-account')).not.toBeNull();
	});

	it('renders no account zone and no account link when accounts are disabled', () => {
		state.accountsEnabled = false;
		const { container } = render(SiteHeader);
		expect(container.querySelector('.site-account')).toBeNull();
		const hrefs = Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href'));
		for (const account of ['/login', '/register', '/settings', '/billing', '/admin', '/mon-espace']) {
			expect(hrefs).not.toContain(account);
		}
		expect(hrefs).toContain('/developpeurs');
	});
});

describe('SiteHeader search (04.7-14, R17; 04.8-02, UI-SPEC v4 § 3.11)', () => {
	it('carries a GET search to the typed results on a fiche, with its shortcut announced (public form)', () => {
		state.pathname = '/commune/44109';
		const { getByRole } = render(SiteHeader, { props: { publique: true } });
		const formulaire = getByRole('search', { name: 'Rechercher une adresse ou une commune' });
		expect(formulaire.getAttribute('action')).toBe('/communes');
		expect(formulaire.getAttribute('method')).toBe('get');
		const champ = getByRole('searchbox', { name: 'Rechercher une adresse ou une commune' });
		expect(champ.getAttribute('name')).toBe('q');
		expect(champ.getAttribute('aria-keyshortcuts')).toBe('Control+K Meta+K');
		expect(champ.getAttribute('maxlength')).toBe('200');
		expect(champ.getAttribute('placeholder')).toBe('Adresse ou commune');
		// The accessible name of the button starts with its visible word (WCAG 2.5.3).
		expect(getByRole('button', { name: 'Rechercher une adresse ou une commune' }).getAttribute('type')).toBe('submit');
	});

	it('connected form (D-27): the search of 04.7-14 as it was, magnifier button, « Ctrl K » hint, its own name', () => {
		state.pathname = '/settings';
		const { getByRole, container } = render(SiteHeader);
		const champ = getByRole('searchbox', { name: 'Rechercher une commune ou une adresse' });
		expect(champ.getAttribute('placeholder')).toBe('Commune ou adresse');
		expect(champ.getAttribute('aria-keyshortcuts')).toBe('Control+K Meta+K');
		expect(container.querySelector('form.recherche-en-tete kbd')?.textContent).toBe('Ctrl K');
		const bouton = getByRole('button', { name: 'Rechercher' });
		expect(bouton.classList.contains('recherche-en-tete-loupe')).toBe(true);
		expect(bouton.querySelector('svg')).not.toBeNull();
		expect(container.querySelector('.recherche-en-tete-publique, .recherche-en-tete-bouton')).toBeNull();
	});

	it('is absent where the page has its own search: / and /communes', () => {
		for (const pathname of ['/', '/communes']) {
			for (const publique of [false, true]) {
				state.pathname = pathname;
				const { queryByRole, unmount } = render(SiteHeader, { props: { publique } });
				expect(queryByRole('search'), `${pathname}, publique ${publique}`).toBeNull();
				unmount();
			}
		}
	});

	it('is absent when the layout asks for none (root error page)', () => {
		state.pathname = '/route-inconnue';
		const { queryByRole } = render(SiteHeader, { props: { publique: true, recherche: false } });
		expect(queryByRole('search')).toBeNull();
	});
});

describe('SiteHeader forms (04.8-02, UI-SPEC v4 § 3.11)', () => {
	const liens = (container: HTMLElement) =>
		Array.from(container.querySelectorAll('nav[aria-label="Navigation principale"] a')).map((a) => [a.textContent?.trim(), a.getAttribute('href')]);

	it('public form: Comparer, Données & méthode, Développeurs, the search before them, no « Rechercher » link', () => {
		state.pathname = '/commune/44109';
		const { container } = render(SiteHeader, { props: { publique: true } });
		expect(liens(container)).toEqual([
			['Comparer', '/comparer'],
			['Données & méthode', '/datasets'],
			['Développeurs', '/developpeurs']
		]);
		const ordre = Array.from(container.querySelectorAll('.site-header-inner > *')).map((el) => el.className.split(' ')[0]);
		// D-41: « Menu » before the search in the DOM, its panel after it, as on screen under 720 px.
		expect(ordre).toEqual(['site-brand', 'site-menu-etroit', 'recherche-en-tete', 'site-end']);
		expect(container.querySelector('.site-dev-link')).toBeNull();
	});

	it('public form (D-41): « Menu » is a native disclosure, folded, whose panel is the group of the links and the account', () => {
		state.pathname = '/commune/44109';
		state.accountsEnabled = true;
		const { container } = render(SiteHeader, { props: { publique: true } });
		const menu = container.querySelector('details.site-menu-etroit');
		expect(menu?.hasAttribute('open')).toBe(false);
		expect(menu?.querySelector('summary')?.textContent?.trim()).toBe('Menu');
		const panneau = menu?.parentElement?.querySelector(':scope > .site-end');
		expect(panneau?.classList.contains('site-end')).toBe(true);
		expect(panneau?.querySelector('nav[aria-label="Navigation principale"]')).not.toBeNull();
		expect(panneau?.querySelector('.site-account')).not.toBeNull();
	});

	it('connected form: no « Menu »', () => {
		state.pathname = '/settings';
		const { container } = render(SiteHeader);
		expect(container.querySelector('details, summary')).toBeNull();
	});

	it('connected form (default): the links of 03-17 and « Développeurs » beside the account', () => {
		state.pathname = '/settings';
		const { container } = render(SiteHeader);
		expect(liens(container)).toEqual([
			['Rechercher', '/'],
			['Comparer', '/comparer'],
			['Données & méthode', '/datasets']
		]);
		expect(container.querySelector('.site-end .site-dev-link')?.getAttribute('href')).toBe('/developpeurs');
	});
});

describe('SiteHeader shortcut (04.7-14, R17)', () => {

	it('Ctrl+K leaves a field being filled alone: no focus move, no navigation', async () => {
		const { goto } = await import('$app/navigation');
		vi.mocked(goto).mockClear();
		state.pathname = '/commune/44109';
		render(SiteHeader);
		document.body.insertAdjacentHTML('beforeend', '<main><form><textarea id="saisie"></textarea></form></main>');
		const saisie = document.getElementById('saisie') as HTMLTextAreaElement;
		saisie.focus();
		const evenement = new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, cancelable: true, bubbles: true });
		saisie.dispatchEvent(evenement);
		await Promise.resolve();
		expect(evenement.defaultPrevented).toBe(false);
		expect(document.activeElement).toBe(saisie);
		expect(goto).not.toHaveBeenCalled();
		document.querySelector('main')?.remove();
	});

	it('Ctrl+K focuses the search of the page when the header field is not displayed', async () => {
		state.pathname = '/';
		render(SiteHeader);
		document.body.insertAdjacentHTML('beforeend', '<main><form role="search"><input id="champ-page" type="text" /></form></main>');
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, cancelable: true }));
		await Promise.resolve();
		expect(document.activeElement?.id).toBe('champ-page');
		document.querySelector('main')?.remove();
	});
});
