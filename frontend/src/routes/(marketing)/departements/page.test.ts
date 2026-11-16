// The list of departements (04.7-10, decision 7): grouped by region, each linked to its page; those
// whose prices are not served say why, from the hosting the API gives, never from a code written here.
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SITE_URL } from '$lib/config';
import { DEPARTEMENTS_LISTE } from '$lib/data/__fixtures__/departement';
import { groupesParRegion, type DepartementsPageData } from '$lib/data/departement';
import { etatLigneDepartement } from '$lib/data/departement-presentation';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://site.invalid/departements'), state: {} })
}));

import { load } from './+page';
import Page from './+page.svelte';

const NBSP = String.fromCharCode(0xa0);
const liste: DepartementsPageData = { etat: 'liste', groupes: groupesParRegion(DEPARTEMENTS_LISTE), total: DEPARTEMENTS_LISTE.length };
const texte = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
const jsonLd = () =>
	[...document.head.querySelectorAll('script[type="application/ld+json"]')].map((script) => JSON.parse(script.textContent ?? '{}'));

afterEach(() => {
	document.head.innerHTML = '';
});

describe('/departements — la liste', () => {
	it('has the title, the breadcrumb and one section per region, in alphabetical order', () => {
		const { container } = render(Page, { props: { data: liste } });
		expect(container.querySelector('h1')?.textContent).toBe('Départements');
		const fil = container.querySelector('nav[aria-label="Fil d\'Ariane"]')!;
		expect([...fil.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([['Accueil', '/']]);
		expect(fil.querySelector('[aria-current="page"]')?.textContent).toBe('Départements');
		expect([...container.querySelectorAll('section.panneau > h2')].map((h) => h.textContent)).toEqual(['Bretagne', 'Corse', 'Pays de la Loire']);
	});

	it('links every departement to its page, its name and its code kept together', () => {
		const { container } = render(Page, { props: { data: liste } });
		const liens = [...container.querySelectorAll('ul.liste-liens a')].map((a) => [a.querySelector('.lien-t')?.textContent, a.getAttribute('href')]);
		expect(liens).toEqual([
			[`Côtes-d'Armor${NBSP}(22)`, '/departement/22'],
			[`Finistère${NBSP}(29)`, '/departement/29'],
			[`Corse-du-Sud${NBSP}(2A)`, '/departement/2A'],
			[`Haute-Corse${NBSP}(2B)`, '/departement/2B'],
			[`Loire-Atlantique${NBSP}(44)`, '/departement/44'],
			[`Vendée${NBSP}(85)`, '/departement/85']
		]);
	});

	it('says why a departement has no prices, from its hosting, and nothing of an indexable one', () => {
		const { container } = render(Page, { props: { data: liste } });
		const ligne = (code: string) => container.querySelector(`a[href="/departement/${code}"]`)!;
		expect(texte(ligne('29').querySelector('.lien-n'))).toBe('Non renseigné'); // D13 (2026-11-10)
		expect(texte(ligne('22').querySelector('.lien-n'))).toBe('Hors couverture · département non hébergé');
		expect(texte(ligne('2A').querySelector('.lien-n'))).toBe('Hors couverture · département non hébergé');
		expect(ligne('44').querySelector('.lien-n')).toBeNull();
		expect(container.textContent).not.toContain('territoire_hors_perimetre');
	});

	it('is canonical to itself, indexable, with a BreadcrumbList', () => {
		render(Page, { props: { data: liste } });
		expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(`${SITE_URL}/departements`);
		expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
		expect(jsonLd().map((o) => o['@type'])).toEqual(['BreadcrumbList']);
		expect(jsonLd()[0].itemListElement.map((e: { name: string }) => e.name)).toEqual(['Accueil', 'Départements']);
	});

	it('writes a title and a meta description of its own', () => {
		render(Page, { props: { data: liste } });
		expect(document.title).toBe('Départements — DataCommune');
		expect(document.head.querySelector('meta[name="description"]')?.getAttribute('content')).toMatch(/départements/);
	});

	// T1 (2026-11-10): the card of a shared link, the tags of the commune fiche (`MetaPartage`), once each.
	it('carries the card of a shared link, each tag once', () => {
		render(Page, { props: { data: liste } });
		const contenu = (selecteur: string) => [...document.head.querySelectorAll(selecteur)].map((m) => m.getAttribute('content'));
		expect(contenu('meta[name="twitter:card"]')).toEqual(['summary_large_image']);
		expect(contenu('meta[name="twitter:title"]')).toEqual(['Départements — DataCommune']);
		expect(contenu('meta[property="og:title"]')).toEqual(['Départements — DataCommune']);
		expect(contenu('meta[property="og:url"]')).toEqual([`${SITE_URL}/departements`]);
		expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
	});
});

describe('/departements — forme direction A (04.8-05)', () => {
	// D12 (2026-11-10, form A): the panels of the regions flow in columns inside one block of the page.
	it('puts every panel of a region in one block that flows in columns', () => {
		const { container } = render(Page, { props: { data: liste } });
		const bloc = container.querySelector('.deps-regions')!;
		expect(bloc).not.toBeNull();
		expect(bloc.querySelectorAll(':scope > section.panneau')).toHaveLength(liste.groupes.length);
	});

	it('has one simple panel per region, named by the served region, each row linked to its departement', () => {
		const { container } = render(Page, { props: { data: liste } });
		const panneaux = [...container.querySelectorAll('section.panneau.panneau-simple')];
		expect(panneaux).toHaveLength(liste.groupes.length);
		panneaux.forEach((panneau, i) => {
			const groupe = liste.groupes[i];
			expect(panneau.querySelector('h2')?.textContent).toBe(groupe.region ?? 'Région non renseignée');
			const liens = [...panneau.querySelectorAll('ul.liste-liens a')];
			expect(liens).toHaveLength(groupe.departements.length);
			liens.forEach((lien, j) => {
				const ligne = groupe.departements[j];
				expect(lien.getAttribute('href')).toBe(`/departement/${ligne.code}`);
				const etat = etatLigneDepartement(ligne);
				if (etat !== null) expect(texte(lien.querySelector('.lien-n')), ligne.code).toBe(etat);
				else expect(lien.querySelector('.lien-n'), ligne.code).toBeNull();
			});
		});
	});
});

describe('/departements — panne', () => {
	const panne: DepartementsPageData = { etat: 'panne', detail: { statut: 503, titre: null, message: null }, retryAfter: null };

	it('shows an alert and no list, no structured data', () => {
		const { container, getByRole } = render(Page, { props: { data: panne } });
		expect(getByRole('alert').textContent).toContain('La liste des départements est momentanément indisponible');
		expect(container.querySelector('ul.liste-liens')).toBeNull();
		expect(jsonLd()).toEqual([]);
		// T2 (2026-11-10): the secondary button of the socle, weight 400.
		expect(getByRole('button', { name: 'Recharger la liste' }).classList.contains('bouton-secondaire')).toBe(true);
	});

	it('is still canonical to itself', () => {
		render(Page, { props: { data: panne } });
		expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(`${SITE_URL}/departements`);
	});
});

describe('/departements — chargement', () => {
	const charger = (fetchImpl: typeof fetch, setHeaders = vi.fn()) =>
		(load as unknown as (e: unknown) => Promise<DepartementsPageData>)({ fetch: fetchImpl, setHeaders });

	it('marks an outage for the 503, and posts nothing for a served list', async () => {
		const setHeaders = vi.fn();
		const panne = vi.fn(async () => new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } })) as unknown as typeof fetch;
		expect((await charger(panne, setHeaders)).etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledWith({ 'x-dc-etat-page': 'panne' });

		const ok = vi.fn(
			async () => new Response(JSON.stringify({ statut: 'ok', donnees: DEPARTEMENTS_LISTE }), { status: 200, headers: { 'Content-Type': 'application/json' } })
		) as unknown as typeof fetch;
		const calme = vi.fn();
		expect((await charger(ok, calme)).etat).toBe('liste');
		expect(calme).not.toHaveBeenCalled();
	});
});
