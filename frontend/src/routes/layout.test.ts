// Root layout and the audience measurement (04.7-14b): with the build switch off nothing is counted;
// on, a page is counted once when it loads and when its path changes, from its matched route.
import { createRawSnippet } from 'svelte';
import { fireEvent, render } from '@testing-library/svelte';
import { readable } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

type Navigation = {
	from: { url: URL | null } | null;
	to: { url: URL; route: { id: string | null }; params: Record<string, string> | null } | null;
};

const etat = vi.hoisted(() => ({
	mesure: true,
	page: { status: 200, url: new URL('http://localhost/'), route: { id: null as string | null }, params: {} as Record<string, string> },
	rappels: [] as ((navigation: Navigation) => void)[],
	compter: vi.fn(async () => undefined),
	recharger: vi.fn(async () => undefined)
}));

vi.mock('$app/navigation', () => ({
	afterNavigate: (rappel: (navigation: Navigation) => void) => etat.rappels.push(rappel),
	goto: vi.fn(),
	invalidateAll: () => etat.recharger()
}));
// The page store is what the layout reads (route, params, status); each navigation sets it first.
vi.mock('$app/stores', () => ({
	page: { subscribe: (run: (value: unknown) => void) => readable(etat.page).subscribe(run) }
}));
vi.mock('$lib/config', async (importOriginal) => {
	const mod = { ...(await importOriginal<typeof import('$lib/config')>()) };
	Object.defineProperty(mod, 'MESURE_AUDIENCE', { get: () => etat.mesure, enumerable: true });
	Object.defineProperty(mod, 'ACCOUNTS_ENABLED', { get: () => false, enumerable: true });
	return mod;
});
vi.mock('$lib/mesure-audience', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/mesure-audience')>()),
	compter: etat.compter
}));

import Layout from './+layout.svelte';
import ErreurRacine from './+error.svelte';
import { EXEMPLES_ACCUEIL } from '$lib/data/recherche-lieux';

const enfants = createRawSnippet(() => ({ render: () => '<p>page</p>' }));

/** Mounts the layout and plays *navigations* through its `afterNavigate` callback. */
function naviguer(...navigations: Navigation[]) {
	render(Layout, { props: { children: enfants } });
	for (const navigation of navigations) {
		// SvelteKit updates the page store before `afterNavigate`; the « enter » target has no route id.
		if (navigation.to) etat.page = { status: 200, url: navigation.to.url, route: navigation.to.route, params: navigation.to.params ?? {} };
		const cible = navigation.from === null && navigation.to ? { ...navigation.to, route: { id: null }, params: null } : navigation.to;
		for (const rappel of etat.rappels) rappel({ ...navigation, to: cible });
	}
}

const page = (chemin: string, id: string | null, params: Record<string, string> = {}) => ({
	url: new URL(`http://localhost${chemin}`),
	route: { id },
	params
});

beforeEach(() => {
	etat.rappels = [];
	etat.compter.mockClear();
});

describe('layout racine — mesure d’audience', () => {
	it('interrupteur de construction coupé : aucun comptage', () => {
		etat.mesure = false;
		naviguer({ from: null, to: page('/adresse/44109_zz04_00004', '/(marketing)/adresse/[id]', { id: '44109_zz04_00004' }) });
		expect(etat.compter).not.toHaveBeenCalled();
	});

	it('interrupteur posé : le chargement compte la route, un changement de requête ne compte pas, un nouveau chemin compte', () => {
		etat.mesure = true;
		const fiche = page('/adresse/44109_zz04_00004', '/(marketing)/adresse/[id]', { id: '44109_zz04_00004' });
		const pageSuivante = page('/adresse/44109_zz04_00004?page=2', '/(marketing)/adresse/[id]', { id: '44109_zz04_00004' });
		naviguer(
			{ from: null, to: fiche },
			{ from: { url: fiche.url }, to: pageSuivante },
			{ from: { url: pageSuivante.url }, to: page('/communes', '/(marketing)/communes') }
		);
		expect(etat.compter.mock.calls).toEqual([['/adresse'], ['/communes']]);
	});

	// Provenance by channel (2026-11-11): the entry page of a visit opened by `?src=<canal>` counts
	// `/venu-de/<canal>` once, after the page itself; the state lives in memory, in the layout.
	it('entrée par un lien de canal connu : la page puis « /venu-de/<canal> », une seule fois pour la visite', () => {
		etat.mesure = true;
		const accueil = page('/?src=reddit', '/(marketing)');
		const communes = page('/communes?src=reddit', '/(marketing)/communes');
		const fiche = page('/commune/44109?src=linkedin', '/(marketing)/commune/[code]', { code: '44109' });
		naviguer({ from: null, to: accueil }, { from: { url: accueil.url }, to: communes }, { from: { url: communes.url }, to: fiche });
		expect(etat.compter.mock.calls).toEqual([['/'], ['/venu-de/reddit'], ['/communes'], ['/commune/44109']]);
	});

	it('entrée sans canal : un canal porté par une page suivante n’est pas compté', () => {
		etat.mesure = true;
		const accueil = page('/', '/(marketing)');
		naviguer({ from: null, to: accueil }, { from: { url: accueil.url }, to: page('/communes?src=reddit', '/(marketing)/communes') });
		expect(etat.compter.mock.calls).toEqual([['/'], ['/communes']]);
	});

	it.each([
		['canal inconnu', '/?src=tiktok'],
		['chemin piégé', '/?src=reddit/../x'],
		['chemin piégé encodé', '/?src=reddit%2F..%2Fx'],
		['majuscules', '/?src=Reddit'],
		['deux paramètres src', '/?src=reddit&src=linkedin']
	])('entrée avec %s (%s) : seule la page est comptée, rien de libre ne part', (_cas, chemin) => {
		etat.mesure = true;
		naviguer({ from: null, to: page(chemin, '/(marketing)') });
		expect(etat.compter.mock.calls).toEqual([['/']]);
	});

	it('une fiche d’adresse ouverte par un lien de canal : « /adresse » puis le canal, jamais l’identifiant', () => {
		etat.mesure = true;
		naviguer({ from: null, to: page('/adresse/44109_zz04_00004?src=mail', '/(marketing)/adresse/[id]', { id: '44109_zz04_00004' }) });
		expect(etat.compter.mock.calls).toEqual([['/adresse'], ['/venu-de/mail']]);
	});

	it('interrupteur coupé : un lien de canal ne compte rien', () => {
		etat.mesure = false;
		naviguer({ from: null, to: page('/?src=reddit', '/(marketing)') });
		expect(etat.compter).not.toHaveBeenCalled();
	});

	it('espace des comptes ouvert par un lien de canal : ni la page ni le canal', () => {
		etat.mesure = true;
		const connexion = page('/login?src=reddit', '/(auth)/login');
		naviguer({ from: null, to: connexion }, { from: { url: connexion.url }, to: page('/?src=reddit', '/(marketing)') });
		expect(etat.compter.mock.calls).toEqual([['/']]);
	});

	// A page rendered on the client only (ssr = false, the (app) group) opens by a client navigation:
	// SvelteKit hands a `from` whose url is still null, not the null `from` of a hydrated page. An error
	// here stops SvelteKit before it starts its router, and no link of the connected space is caught.
	it('chargement direct d’une page rendue côté client : un `from` sans url ne lève rien', () => {
		etat.mesure = true;
		expect(() => naviguer({ from: { url: null }, to: page('/settings', '/(app)/settings') })).not.toThrow();
		expect(etat.compter).not.toHaveBeenCalled();
	});
});

// 04.8-07 (UI-SPEC § 6.10, § 7.4): the root error page in its v4 form.
describe('page d’erreur racine (04.8-07)', () => {
	const erreur = (status: number, message?: string) => {
		etat.page = { ...page('/route-inconnue', null), status, error: message ? { message } : null } as unknown as typeof etat.page;
		return render(ErreurRacine).container;
	};

	it('404 : h1, chapô en deux phrases, grand champ, « Pour repartir » (l’accueil puis les entrées génériques)', () => {
		const racine = erreur(404, 'Not Found');
		expect(racine.querySelector('h1')?.textContent).toBe('Page introuvable');
		const textes = [...racine.querySelectorAll('main p')].map((p) => p.textContent?.trim());
		expect(textes).toContain(
			'Cette adresse ou cette page n’existe pas sur DataCommune. Le lien est peut-être incomplet, ou l’adresse ne figure pas encore dans la Base Adresse Nationale.'
		);
		expect(textes).toContain('Une adresse récente peut y manquer quelque temps.');
		const formulaire = racine.querySelector('main form[action="/communes"]')!;
		expect(formulaire.getAttribute('method')?.toLowerCase()).toBe('get');
		expect(formulaire.querySelector('label')?.textContent).toContain('Chercher une adresse ou une commune');
		const repartir = [...racine.querySelectorAll('main section')].find((s) => s.querySelector('h2')?.textContent === 'Pour repartir')!;
		expect([...repartir.querySelectorAll('a')].map((a) => [a.querySelector('.lien-t')?.textContent, a.getAttribute('href')])).toEqual([
			['L’accueil', '/'],
			...EXEMPLES_ACCUEIL.map((e) => [e.label, e.href])
		]);
		// No breadcrumb and no header search; no place written.
		expect(racine.querySelector('nav[aria-label="Fil d\u2019Ariane"], nav[aria-label="Fil d\'Ariane"]')).toBeNull();
		expect(racine.querySelector('header [role="search"]')).toBeNull();
		// The departement filter of the field lists every departement (static referential): out of the check.
		const main = racine.querySelector('main')!.cloneNode(true) as HTMLElement;
		main.querySelectorAll('select').forEach((select) => select.remove());
		expect(main.textContent).not.toMatch(/Loire-Atlantique|Vendée|Nantes|Luçon/);
	});

	it('autre erreur : « Une erreur est survenue », texte d’aujourd’hui, même gabarit', () => {
		const racine = erreur(500, 'Internal Error');
		expect(racine.querySelector('h1')?.textContent).toBe('Une erreur est survenue');
		expect(racine.querySelector('main .erreur-statut')?.textContent).toBe('Erreur 500');
		expect(racine.textContent).toContain("Le service n'a pas répondu. Réessayez dans un instant.");
		expect(racine.querySelector('main form[action="/communes"]')).not.toBeNull();
		expect(racine.querySelector('main .simple')).not.toBeNull();
	});

	// T9 (inventory 2026-11-10): the departement filter left the home page on 09/11; the 404 field follows.
	it('404 : le champ n’a pas de filtre « Département » (retiré de l’accueil le 09/11)', () => {
		const racine = erreur(404, 'Not Found');
		expect(racine.querySelector('main form[action="/communes"]')).not.toBeNull();
		expect(racine.querySelector('main select, main [name="departement"]')).toBeNull();
		expect(racine.querySelector('main')!.textContent).not.toContain('Département');
	});

	// I8: a 429 or a 5xx offers the reload the address and DPE pages already had; a 404 has nothing to reload.
	it.each([429, 500, 503])('erreur %i : « Recharger la page » relance le chargement', async (status) => {
		const racine = erreur(status, status === 429 ? 'Trop de requêtes.' : undefined);
		const bouton = [...racine.querySelectorAll('main button')].find((b) => b.textContent === 'Recharger la page') as HTMLButtonElement;
		expect(bouton).toBeDefined();
		expect(bouton.classList.contains('bouton-secondaire')).toBe(true);
		etat.recharger.mockClear();
		await fireEvent.click(bouton);
		expect(etat.recharger).toHaveBeenCalledTimes(1);
	});

	it('404 : aucun bouton de relance', () => {
		const racine = erreur(404, 'Not Found');
		expect([...racine.querySelectorAll('main button')].map((b) => b.textContent)).not.toContain('Recharger la page');
	});

	it('un message servi par un chargement est rendu tel quel', () => {
		const racine = erreur(404, 'Aucune commune ne porte ce code.');
		expect(racine.textContent).toContain('Aucune commune ne porte ce code.');
	});

	// 04.8-08 (D-31): no register here, the lead is spelled out on its own; the tab title is not.
	it('repli des sigles : le sigle d’un message servi est développé dans le chapô, pas dans le titre de l’onglet', () => {
		const racine = erreur(404, 'Aucun DPE ne porte ce numéro.');
		expect(racine.querySelector('main .erreur-chapo')?.textContent).toBe('Aucun DPE (diagnostic de performance énergétique) ne porte ce numéro.');
		expect(racine.querySelector('main h1')?.textContent).toBe('Page introuvable');
		expect(document.title).not.toMatch(/\(diagnostic/);
	});
});
