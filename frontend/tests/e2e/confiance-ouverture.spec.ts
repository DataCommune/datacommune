// 04.7-14 « Confiance et ouverture », in a blank browser context each time (headless runner):
//   - consent (Origami F17, R2): no request leaves the site and its API, no cookie but the declared
//     `dc_session` (only under /adresse, HttpOnly, Lax, seven days at most), no storage key the
//     privacy policy does not describe — before any interaction;
//   - the header search and Ctrl+K (R17), the open JSON export of a commune and `/llms.txt` (R5);
//   - a route 404 that offers a next step (F8), an empty search that proposes one (R16).
import { readFileSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Browser } from '@playwright/test';
import { PREMIERE_PAGE_DECLAREES } from './fixtures/adresse-ventes';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './fixtures/commune-indicators';
import { DOCUMENT_REEL, REEL } from './fixtures/dpe-documents';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';

interface Adresse {
	banId: string;
	rapport: { donnees: { adresse: { libelle: string } } };
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}

const [ADRESSE] = JSON.parse(readFileSync(new URL('./fixtures/rapports-maquette.json', import.meta.url), 'utf8')) as Adresse[];
const ORIGINES_DU_SITE = new Set([E2E_APP_ORIGIN, E2E_APP_ORIGIN.replace('localhost', '127.0.0.1'), E2E_SOURCE_ORIGIN]);
/** Keys of the browser storage the privacy policy describes (selection of « Comparer », recent communes). */
const CLES_DECLAREES = /^dc:(comparaison(:adresses|:dpe|:derniere-nature)?|recent-fiches)$/;
const SEPT_JOURS_S = 7 * 24 * 60 * 60;
const NANTES = {
	code_insee: '44109',
	nom: 'Nantes',
	code_postal: '44000',
	departement: '44',
	population: 320732,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: { etat: 'heberge', motif: null, departements: ['44'] }
};

const ouvert = (pathname: string, query: Record<string, string>, body: unknown, status = 200) => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 0,
	maxCalls: 20,
	responses: Array.from({ length: 20 }, () => ({ status, body }))
});
const adressesVides = { statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: { etat: 'heberge', motif: null, departements: ['44'] } } } };

async function configurer(request: APIRequestContext) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				ouvert(cheminRapport(ADRESSE.banId), REQUETE_RAPPORT, ADRESSE.rapport),
				ouvert(`/v1/adresse/${ADRESSE.banId}/dpe`, { page: '1' }, ADRESSE.documents),
				ouvert(`/v1/adresse/${ADRESSE.banId}/ventes`, { page: '1' }, ADRESSE.ventes),
				ouvert(`/v1/adresse/${ADRESSE.banId}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, ADRESSE.ventesAdresse),
				ouvert('/v1/commune', { code_insee: '85191' }, { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } }),
				ouvert('/v1/communes/search', { q: 'Nantesss', limit: '20', inclure_arrondissements: 'true' }, { statut: 'ok', donnees: [] }),
				ouvert('/v1/communes/search', { q: 'Nantes', limit: '5', inclure_arrondissements: 'true' }, { statut: 'ok', donnees: [NANTES] }),
				ouvert('/v1/adresses/recherche', { q: 'Nantesss', limite: '8' }, adressesVides),
				ouvert(`/v1/dpe/${REEL}`, {}, DOCUMENT_REEL)
			]
		}
	});
	expect(res.ok()).toBe(true);
}

/** Opens *chemin* in a context of its own and reports what it left and what it asked for, untouched. */
async function lireEnContexteVierge(browser: Browser, chemin: string) {
	const contexte = await browser.newContext();
	const page = await contexte.newPage();
	const requetes: string[] = [];
	page.on('request', (r) => requetes.push(r.url()));
	const reponse = await page.goto(chemin, { waitUntil: 'networkidle' });
	const stockage = await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)]);
	const cookies = await contexte.cookies();
	await contexte.close();
	return { statut: reponse?.status() ?? 0, requetes, stockage, cookies };
}

test.describe('Consentement en contexte vierge (R2, F17)', () => {
	test.beforeEach(async ({ request }) => configurer(request));

	for (const chemin of [
		'/',
		'/commune/85191',
		`/adresse/${ADRESSE?.banId}`,
		`/dpe/${REEL}`,
		'/communes?q=Nantesss',
		'/ce-que-nous-ne-montrons-pas',
		'/mentions-legales'
	]) {
		test(`${chemin} : aucune requête tierce, aucun cookie hors du cookie déclaré, aucun stockage non déclaré`, async ({ browser }) => {
			const lu = await lireEnContexteVierge(browser, chemin);
			expect(lu.statut, chemin).toBe(200);
			const tierces = lu.requetes.filter((url) => !url.startsWith('data:') && !ORIGINES_DU_SITE.has(new URL(url).origin));
			expect(tierces, 'requêtes vers une origine tierce').toEqual([]);
			expect(lu.stockage.filter((cle) => !CLES_DECLAREES.test(cle)), 'clés de stockage non déclarées').toEqual([]);
			if (chemin.startsWith('/adresse/')) {
				expect(lu.cookies.map((c) => c.name)).toEqual(['dc_session']);
				const [session] = lu.cookies;
				expect(session).toMatchObject({ path: '/adresse', httpOnly: true, sameSite: 'Lax' });
				expect(session.expires - Date.now() / 1000).toBeLessThanOrEqual(SEPT_JOURS_S + 60);
			} else {
				expect(lu.cookies, `aucun cookie sur ${chemin}`).toEqual([]);
			}
		});
	}

	test('la politique de confidentialité nomme le seul cookie et le stockage du navigateur', async ({ page }) => {
		await page.goto('/confidentialite');
		// One space for every run of spaces, line breaks and no-break spaces of the markup.
		const texte = ((await page.getByRole('main').textContent()) ?? '').replace(/\s+/g, ' ');
		expect(texte).toContain('dc_session');
		expect(texte).toContain('sélection de la page « Comparer »');
		expect(texte).toContain('dernières communes consultées');
		expect(texte).toContain("elle ne dépose aucun cookie et n'écrit rien dans votre navigateur");
		expect(texte).toContain('votre navigateur ne contacte jamais l’IGN');
	});
});

/** Opens *chemin* and waits for the hydration (the session read of the header store). */
async function ouvrirHydratee(page: import('@playwright/test').Page, chemin: string) {
	const hydratee = page.waitForResponse((r) => new URL(r.url()).pathname === '/auth/me');
	await page.goto(chemin);
	await hydratee;
}

/** Height of the site header at the current width. */
const hauteurEnTete = async (page: import('@playwright/test').Page) => (await page.locator('header.site-header').boundingBox())!.height;

test.describe('Recherche dans l’en-tête, Ctrl+K (R17)', () => {
	test.beforeEach(async ({ request }) => configurer(request));

	// 04.8-02 (UI-SPEC v4 § 3.11): the header field is shown at every width, except on `/`, `/communes`
	// and the root error page; its name is « Rechercher une adresse ou une commune ».
	test('1440 : Ctrl+K met le focus sur le champ de l’en-tête, Entrée mène aux résultats', async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await ouvrirHydratee(page, '/ce-que-nous-ne-montrons-pas');
		await page.keyboard.press('Control+k');
		const champ = page.getByRole('searchbox', { name: 'Rechercher une adresse ou une commune' });
		await expect(champ).toBeFocused();
		await champ.fill('Nantesss');
		await page.keyboard.press('Enter');
		await expect(page).toHaveURL(/\/communes\?q=Nantesss$/);
	});

	test('375 : Ctrl+K met le focus sur le champ de l’en-tête ; sans champ (page introuvable), il ouvre la recherche de l’accueil', async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		await ouvrirHydratee(page, '/ce-que-nous-ne-montrons-pas');
		await page.keyboard.press('Control+k');
		await expect(page.getByRole('searchbox', { name: 'Rechercher une adresse ou une commune' })).toBeFocused();
		// Before 04.8-02 the field was hidden under 1200 px; the page without a header field is now the
		// root error page, where the shortcut still opens the search of `/`.
		await ouvrirHydratee(page, '/route-inconnue-raccourci');
		await expect(page.getByRole('heading', { level: 1, name: 'Page introuvable' })).toBeVisible();
		await expect(page.getByRole('banner').getByRole('search')).toHaveCount(0);
		await page.keyboard.press('Control+k');
		// 04.8-07 (UI-SPEC § 6.10): the root 404 now carries the large field in its body, so the shortcut
		// focuses that search of the page (the « main search » branch of SiteHeader) instead of opening `/`.
		await expect(page).toHaveURL(`${E2E_APP_ORIGIN}/route-inconnue-raccourci`);
		await expect(
			page.getByRole('main').getByRole('search', { name: 'Rechercher un lieu' }).getByRole('combobox', { name: 'Chercher une adresse ou une commune' })
		).toBeFocused();
	});

	test('sans JavaScript, le formulaire de l’en-tête mène aux résultats', async ({ browser }) => {
		const contexte = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1440, height: 900 } });
		const page = await contexte.newPage();
		await page.goto('/mentions-legales');
		await page.getByRole('searchbox', { name: 'Rechercher une adresse ou une commune' }).fill('Nantesss');
		await page.getByRole('button', { name: 'Rechercher une adresse ou une commune', exact: true }).click();
		await expect(page).toHaveURL(/\/communes\?q=Nantesss$/);
		await contexte.close();
	});

	for (const largeur of [1440, 1200, 1024, 375, 320]) {
		test(`${largeur} px : en-tête d’une fiche sans débordement, une rangée de plus au plus que celui de l’accueil, axe sans violation`, async ({ page }) => {
			await page.setViewportSize({ width: largeur, height: 900 });
			// The home page has no header field: its header is the reference height at this width.
			await ouvrirHydratee(page, '/');
			const reference = await hauteurEnTete(page);
			await ouvrirHydratee(page, '/commune/85191');
			await expect(page.getByRole('searchbox', { name: 'Rechercher une adresse ou une commune' })).toBeVisible();
			const { large, vue } = await page.evaluate(() => ({ large: document.documentElement.scrollWidth, vue: innerWidth }));
			expect(large).toBeLessThanOrEqual(vue);
			// 4.9 harmonisation (amends § 3.11): above 720 px the header holds one row at every width, the field
			// adds no height (+56 at 1024 px before); up to 720 px it has two rows (D-41, measured +52 at 375 and
			// 320 px). Exactly that: a field that would not add its row is a change to see (Opus 02-1 P3-11).
			const hauteur = await hauteurEnTete(page);
			const rangee = largeur > 720 ? 0 : 52;
			expect(hauteur, `hauteur ${hauteur} px, accueil ${reference} px`).toBe(reference + rangee);
			const axe = await new AxeBuilder({ page }).include('header.site-header').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
			expect(axe.violations.map((v) => v.id)).toEqual([]);
		});
	}
});

test.describe('Ouverture aux machines (R5)', () => {
	test.beforeEach(async ({ request }) => configurer(request));

	test('/commune/{code}.json : la lecture de la page, ses sources, non indexée, lisible de partout', async ({ request }) => {
		const res = await request.get('/commune/85191.json');
		expect(res.status()).toBe(200);
		expect(res.headers()['x-robots-tag']).toBe('noindex');
		expect(res.headers()['access-control-allow-origin']).toBe('*');
		const corps = await res.json();
		expect(corps).toMatchObject({ format: 'datacommune.commune.v1', code_insee: '85191', etat: 'fiche', sources: COMMUNE_85191_SOURCES });
		expect(corps.donnees.code_insee).toBe('85191');
	});

	test('la fiche commune lie son export JSON', async ({ page }) => {
		await page.goto('/commune/85191');
		await expect(page.getByRole('link', { name: 'Télécharger (JSON)' })).toHaveAttribute('href', '/commune/85191.json');
		await expect(page.getByRole('link', { name: 'Ce que cette fiche ne montre pas' })).toHaveAttribute('href', '/ce-que-nous-ne-montrons-pas');
	});

	test('/llms.txt : texte, modèles d’URL, limites et attribution', async ({ request }) => {
		const res = await request.get('/llms.txt');
		expect(res.status()).toBe(200);
		expect(res.headers()['content-type']).toContain('text/plain');
		const texte = await res.text();
		for (const attendu of ['# DataCommune', '/commune/{code_insee}.json', '/ce-que-nous-ne-montrons-pas', '## Attribution']) {
			expect(texte).toContain(attendu);
		}
	});
});

test.describe('Impasses : 404 et recherche vide (F8, R16)', () => {
	test.beforeEach(async ({ request }) => configurer(request));

	test('une route inconnue : 404 « Page introuvable », la recherche et des exemples', async ({ page }) => {
		const reponse = await page.goto('/route-que-le-site-ne-connait-pas');
		expect(reponse?.status()).toBe(404);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page introuvable');
		// 04.8-07 (UI-SPEC § 6.10, D-34): the search is the large field of the page; « Revenir à la recherche »
		// becomes « L’accueil », first row of « Pour repartir »; the examples are the generic entries of the
		// home, no place written by hand (was « La Roche-sur-Yon »).
		await expect(page.getByRole('main').getByRole('combobox', { name: 'Chercher une adresse ou une commune' })).toBeVisible();
		const repartir = page.getByRole('main').getByRole('region', { name: 'Pour repartir' });
		await expect(repartir.getByRole('link').first()).toHaveAccessibleName(/^L’accueil/);
		await expect(repartir.getByRole('link').first()).toHaveAttribute('href', '/');
		await expect(repartir.getByRole('link', { name: /^Parcourir l’annuaire des communes/ })).toHaveAttribute('href', '/communes');
		await expect(page.getByRole('main')).not.toContainText('La Roche-sur-Yon');
	});

	for (const largeur of [1440, 375, 320]) {
		test(`${largeur} px : une recherche sans résultat propose les communes au nom proche et des exemples`, async ({ page }, testInfo) => {
			await page.setViewportSize({ width: largeur, height: 900 });
			const reponse = await page.goto('/communes?q=Nantesss');
			expect(reponse?.status()).toBe(200);
			const suite = page.locator('.communes-suite');
			await expect(suite).toContainText('Communes dont le nom commence par « Nantes »');
			await expect(suite.getByRole('link', { name: 'Nantes', exact: true })).toHaveAttribute('href', '/commune/44109');
			// 04.8-07 (D-34): the examples are the generic entries of the home, no place written by hand
			// (was « La Roche-sur-Yon »).
			await expect(suite.getByRole('link', { name: 'Parcourir l’annuaire des communes', exact: true })).toHaveAttribute('href', '/communes');
			await expect(suite.getByRole('link', { name: 'La Roche-sur-Yon', exact: true })).toHaveCount(0);
			const { large, vue } = await page.evaluate(() => ({ large: document.documentElement.scrollWidth, vue: innerWidth }));
			expect(large).toBeLessThanOrEqual(vue);
			const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
			expect(axe.violations.map((v) => v.id)).toEqual([]);
			await page.screenshot({ path: testInfo.outputPath(`recherche-vide-${largeur}.png`), fullPage: true, animations: 'disabled' });
		});
	}
});

test('captures de la page « Ce que nous ne montrons pas » et des mentions', async ({ page }, testInfo) => {
	for (const chemin of ['/ce-que-nous-ne-montrons-pas', '/mentions-legales']) {
		for (const largeur of [1440, 375, 320]) {
			await page.setViewportSize({ width: largeur, height: 900 });
			// After the hydration: an element shot while it is being replaced is detached (flaky capture).
			await ouvrirHydratee(page, chemin);
			await page.getByRole('main').screenshot({ path: testInfo.outputPath(`${chemin.slice(1)}-${largeur}.png`), animations: 'disabled' });
		}
	}
});

