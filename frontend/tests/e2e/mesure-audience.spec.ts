// Audience measurement (04.7-14b, audit A9), as the public build runs it (VITE_MESURE_AUDIENCE=true,
// /count answered 204 by the dev server): one count per page shown, on the site's own origin, with a
// path built from the matched route only; never a query string, a title, a referrer or a cookie; no
// count on a change of query alone; an error page counted by its kind; the account space never.
// A browser driven by WebDriver sends nothing (`compter`): the counting tests present a browser that
// is not driven, one test keeps the driven one. The specs do not depend on /auth/me, so they also run
// with the accounts switched off, as in production (VITE_ACCOUNTS_ENABLED=false).
import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page, type Request } from '@playwright/test';
import { PREMIERE_PAGE_DECLAREES } from './fixtures/adresse-ventes';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './fixtures/commune-indicators';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';

interface Adresse {
	banId: string;
	rapport: unknown;
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}

const [ADRESSE] = JSON.parse(readFileSync(new URL('./fixtures/rapports-maquette.json', import.meta.url), 'utf8')) as Adresse[];

const ouvert = (pathname: string, query: Record<string, string>, body: unknown) => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 0,
	maxCalls: 20,
	responses: Array.from({ length: 20 }, () => ({ status: 200, body }))
});

async function configurer(request: APIRequestContext) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				ouvert(cheminRapport(ADRESSE.banId), REQUETE_RAPPORT, ADRESSE.rapport),
				ouvert(`/v1/adresse/${ADRESSE.banId}/dpe`, { page: '1' }, ADRESSE.documents),
				ouvert(`/v1/adresse/${ADRESSE.banId}/ventes`, { page: '1' }, ADRESSE.ventes),
				ouvert(`/v1/adresse/${ADRESSE.banId}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, ADRESSE.ventesAdresse),
				ouvert('/v1/commune', { code_insee: '85191' }, { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } })
			]
		}
	});
	expect(res.ok()).toBe(true);
}

/** Every count the page sends, as requested by the browser. */
function comptages(page: Page): Request[] {
	const vus: Request[] = [];
	page.on('request', (requete) => {
		if (new URL(requete.url()).pathname === '/count') vus.push(requete);
	});
	return vus;
}

const chemins = (vus: Request[]) => vus.map((r) => new URL(r.url()).searchParams.get('p'));

/** A browser that is not driven by WebDriver, as a visitor's is. */
async function navigateurOrdinaire(page: Page) {
	await page.addInitScript(() => Object.defineProperty(navigator, 'webdriver', { get: () => false }));
}

test.beforeEach(async ({ request }) => configurer(request));

test('une fiche d’adresse est comptée « /adresse », sur le domaine du site, sans rien d’autre', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	await page.goto(`/adresse/${ADRESSE.banId}`);
	await expect.poll(() => vus.length).toBe(1);
	const [requete] = vus;
	const url = new URL(requete.url());
	expect(url.origin).toBe(E2E_APP_ORIGIN);
	expect([...url.searchParams.keys()]).toEqual(['p']);
	expect(url.searchParams.get('p')).toBe('/adresse');
	const entetes = await requete.allHeaders();
	expect(entetes.referer, 'aucun référent').toBeUndefined();
	expect(entetes.cookie, 'aucun cookie').toBeUndefined();
});

test('une URL encodée qui atteint la fiche d’adresse est comptée « /adresse », sans l’identifiant (Codex 14b n° 1)', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	const reponse = await page.goto(`/%61dresse/${ADRESSE.banId}`);
	expect(reponse?.status()).toBe(200);
	await expect.poll(() => chemins(vus)).toEqual(['/adresse']);
	expect(vus[0].url()).not.toContain(ADRESSE.banId);
});

test('un navigateur piloté par WebDriver n’envoie aucun comptage (Codex 14b n° 3)', async ({ page }) => {
	const vus = comptages(page);
	await page.goto(`/adresse/${ADRESSE.banId}`);
	await page.getByRole('heading', { level: 1 }).waitFor();
	await page.goto('/communes?q=Nantes');
	await page.getByRole('heading', { level: 1 }).waitFor();
	expect(vus).toEqual([]);
});

test('l’espace des comptes n’est jamais compté, ouvert ou fermé (Codex 14b n° 7)', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	await page.goto('/login');
	await page.getByRole('heading', { level: 1 }).waitFor();
	await page.goto('/');
	await expect.poll(() => chemins(vus)).toEqual(['/']);
});

test('une recherche est comptée « /communes », sans sa requête', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	await page.goto('/communes?q=4%20rue%20Jean%20Jaur%C3%A8s');
	await expect.poll(() => chemins(vus)).toEqual(['/communes']);
	expect(vus[0].url()).not.toContain('Jaur');
});

test('un changement de requête seul ne compte pas ; un changement de chemin compte une fois', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	await page.goto('/commune/85191');
	// The first count is sent once the page is hydrated: client navigation is ready from here.
	await expect.poll(() => chemins(vus)).toEqual(['/commune/85191']);
	// 04.8-04 (UI-SPEC v4 § 6.2): the theme links (`?theme=`) are gone, every theme is an open panel; moving
	// inside the fiche to a theme anchor changes the URL, not the path: the page is not counted again.
	await page.evaluate(() => {
		location.hash = 'immobilier';
	});
	await expect(page).toHaveURL(/#immobilier$/);
	await expect(page.locator('#critere-prix_commune')).toBeInViewport();
	// Back to the search by the breadcrumb: a new path, one new count.
	await page.getByRole('navigation', { name: "Fil d'Ariane" }).getByRole('link', { name: 'Accueil' }).click();
	await expect(page).toHaveURL(`${E2E_APP_ORIGIN}/`);
	await expect.poll(() => chemins(vus)).toEqual(['/commune/85191', '/']);
});

// Provenance by channel (2026-11-11): a link spread by the publisher carries `?src=<canal>`. The entry
// page counts `/venu-de/<canal>` once more when the channel is in the closed list; the referrer is
// never read, nothing typed in the URL is sent, and the channel is no part of the address of the page.
test('entrée par un lien de canal : la page puis « /venu-de/reddit », une seule fois ; l’adresse canonique ignore le canal', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	await page.goto('/commune/85191?src=reddit');
	await expect.poll(() => chemins(vus)).toEqual(['/commune/85191', '/venu-de/reddit']);
	for (const requete of vus) {
		const url = new URL(requete.url());
		expect(url.origin).toBe(E2E_APP_ORIGIN);
		expect([...url.searchParams.keys()]).toEqual(['p']);
		const entetes = await requete.allHeaders();
		expect(entetes.referer, 'aucun référent').toBeUndefined();
		expect(entetes.cookie, 'aucun cookie').toBeUndefined();
	}
	// The canonical address and the card of a shared link are those of the page, without the channel.
	await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute('href', 'https://datacommune.fr/commune/85191');
	await expect(page.locator('head meta[property="og:url"]')).toHaveAttribute('content', 'https://datacommune.fr/commune/85191');
	// Nothing of the measurement is written in the browser: no cookie, no storage key of its own.
	expect(await page.context().cookies()).toEqual([]);
	expect(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].filter((cle) => /canal|venu|src|mesure/i.test(cle)))).toEqual([]);
	// The next page of the visit is counted alone: the channel is counted at the entry only.
	await page.getByRole('navigation', { name: "Fil d'Ariane" }).getByRole('link', { name: 'Accueil' }).click();
	await expect(page).toHaveURL(`${E2E_APP_ORIGIN}/`);
	await expect.poll(() => chemins(vus)).toEqual(['/commune/85191', '/venu-de/reddit', '/']);
});

test('une recherche ouverte par un lien de canal marche, et sa carte de partage garde la requête sans le canal', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	await page.goto('/communes?q=Nantes&src=linkedin');
	await expect.poll(() => chemins(vus)).toEqual(['/communes', '/venu-de/linkedin']);
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await expect(page).toHaveURL(`${E2E_APP_ORIGIN}/communes?q=Nantes&src=linkedin`);
	const carte = await page.locator('head meta[property="og:url"]').getAttribute('content');
	expect(carte).toBe('https://datacommune.fr/communes?q=Nantes');
	for (const requete of vus) expect(requete.url()).not.toContain('Nantes');
});

test('canal inconnu ou valeur piégée : seule la page est comptée, rien de libre ne part', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	const pieges = ['/?src=tiktok', '/?src=reddit/../x', '/?src=reddit%2F..%2Fx', '/?src=Reddit', '/?src=reddit&src=linkedin', '/?src='];
	for (const [rang, piege] of pieges.entries()) {
		await page.goto(piege);
		await expect.poll(() => chemins(vus), piege).toEqual(Array(rang + 1).fill('/'));
	}
	// A known channel last: its count closes the list, so a late count of a trap would stand before it.
	await page.goto('/?src=mail');
	await expect.poll(() => chemins(vus)).toEqual([...pieges.map(() => '/'), '/', '/venu-de/mail']);
	for (const requete of vus) expect(requete.url()).not.toMatch(/tiktok|linkedin|\.\.|Reddit/);
});

test('un navigateur piloté ouvert par un lien de canal n’envoie rien non plus', async ({ page }) => {
	const vus = comptages(page);
	await page.goto('/?src=reddit');
	await page.getByRole('heading', { level: 1 }).waitFor();
	await page.goto('/communes?q=Nantes');
	await page.getByRole('heading', { level: 1 }).waitFor();
	expect(vus).toEqual([]);
});

test('une page d’erreur est comptée par son genre, jamais par l’URL saisie', async ({ page }) => {
	await navigateurOrdinaire(page);
	const vus = comptages(page);
	const reponse = await page.goto('/12-rue-de-la-paix-nantes');
	expect(reponse?.status()).toBe(404);
	await expect.poll(() => chemins(vus)).toEqual(['/erreur-404']);
	expect(vus[0].url()).not.toContain('paix');
});
