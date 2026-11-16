import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { configureSsrSource, type SsrSourceProbe } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047 } from './fixtures/commune-indicators';

// Phase 42 « UI confiance » — UAT gate (Lot 0 du plan UI 2026-07-04).
//
// Couvre les 4 success criteria de la phase en navigateur réel :
//   1. bandeau factuel de couverture (plus de bandeau d'excuse) ;
//   2. (retiré en 03-03 avec la recherche de l'en-tête, hypothèse H7 du UI-SPEC) ;
//   3. fiche commune : bouton copier INSEE, fil d'Ariane, compteur de profondeur égal au modèle ;
//   4. (le crawl nav reste couvert par nav-crawl.spec.ts — rien ici ne casse un lien).
//
// Même politique que fiche-commune.spec.ts : le contrat /v1/commune est mocké
// (contract-shaped, jamais asserté comme donnée de production) pour prouver le
// CÂBLAGE indépendamment de la dispo du backend local.

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z'
};

// Contract-shaped synthese: every flat column as served since D-47, each null one with its
// declared state (03-07). The depth counter is compared with the data layer run on this very
// response, never with a figure written here.
const MOCK_COMMUNE_85047 = {
	statut: 'ok',
	donnees: [COMMUNE_FLAT_85047],
	meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
};

async function mockCommune(page: import('@playwright/test').Page): Promise<SsrSourceProbe> {
	return configureSsrSource(page.request, [
		{
			method: 'GET',
			pathname: '/v1/commune',
			query: { code_insee: '85047' },
			minCalls: 1,
			maxCalls: 1,
			responses: [{ status: 200, body: MOCK_COMMUNE_85047 }]
		}
	]);
}

// Réponse de `/v1/datasets` (fixture générée par le backend) servie sur deux départements
// arbitraires : l'écran ne connaît pas le périmètre de l'instance (D-34, D-35).
const DATASETS_API = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')
) as { donnees: Array<{ hebergement: { etat: string; departements: string[] } | null }> };
const DATASETS_2A_974 = {
	...DATASETS_API,
	donnees: DATASETS_API.donnees.map((jeu) =>
		jeu.hebergement?.etat === 'heberge'
			? { ...jeu, hebergement: { ...jeu.hebergement, departements: ['2A', '974'] } }
			: jeu
	)
};

async function mockAuthAnonymous(page: import('@playwright/test').Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({
			status: 401,
			contentType: 'application/json',
			body: JSON.stringify({ detail: 'Non authentifié' })
		})
	);
}

async function mockAuthLoggedIn(page: import('@playwright/test').Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) })
	);
}

test.describe('Critère 1 — bandeau factuel de couverture', () => {
	// 03-03 (D-34) : le ruban du layout racine, qui écrivait « Loire-Atlantique & Vendée » en dur,
	// a disparu avec son cas. 03-05 (D-38) : la landing qui énonçait « Démo 44 & 85 » et ses
	// compteurs est remplacée par la recherche directe. L'invariant (le visiteur peut savoir ce
	// que couvre le site) est porté par le lien « Voir la couverture des données » de `/`
	// vers `/datasets` (section lue dans l'API, 03-11) ; aucun département n'est écrit en dur.
	test('la home renvoie vers ce que couvre l’instance, plus aucun bandeau d’excuse', async ({
		page
	}) => {
		await mockAuthAnonymous(page);
		await page.goto('/');

		await expect(page.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute(
			'href',
			'/datasets#couverture-titre'
		);
		await expect(page.getByText('en cours de développement')).toHaveCount(0);

		// Texte de la page hors formulaire de recherche : le filtre département liste tous les
		// départements français (référentiel statique, pas le périmètre hébergé, D-34).
		const texte = await page.locator('main').evaluate((main) => {
			const clone = main.cloneNode(true) as HTMLElement;
			clone.querySelectorAll('form, script, style').forEach((el) => el.remove());
			return clone.textContent ?? '';
		});
		// 04.8-07 (UI-SPEC § 6.6): the chapô of the v4 head replaces the former lead.
		expect(texte, 'texte vide — le contrôle serait vacuous').toContain(
			'Chaque chiffre vient d’une source publique et dit jusqu’où il vaut'
		);
		expect(texte).not.toMatch(/Loire-Atlantique|Vendée|\b44\b|\b85\b/);
	});

	// 03-11 (D-40) : « Données & méthode » dit ce que couvre l'instance, lu dans `/v1/datasets`.
	// Le lien de l'en-tête et celui du pied y mènent ; la section de couverture montre les
	// départements servis par la réponse (ici 2A et 974, arbitraires), jamais un périmètre écrit.
	for (const zone of ['banner', 'contentinfo'] as const) {
		test(`le lien « Données & méthode » (${zone === 'banner' ? 'en-tête' : 'pied'}) mène à la couverture servie`, async ({
			page
		}) => {
			await mockAuthAnonymous(page);
			const source = await configureSsrSource(page.request, [
				{
					method: 'GET',
					pathname: '/v1/datasets',
					query: {},
					minCalls: 1,
					maxCalls: 1,
					responses: [{ status: 200, body: DATASETS_2A_974 }]
				}
			]);
			await page.goto('/');
			await page.getByRole(zone).getByRole('link', { name: 'Données & méthode', exact: true }).click();

			await expect(page.getByRole('heading', { level: 1 })).toHaveText('Données & méthode');
			const couverture = page.getByRole('region', { name: 'Couverture', exact: true });
			// Lot 8 de la 4.9 : la liste est repliée, un clic l'ouvre.
			await couverture.getByText('Voir la liste des départements', { exact: true }).click();
			await expect(couverture.getByRole('listitem')).toHaveText(['Corse-du-Sud (2A)', 'La Réunion (974)']);
			await source.assert();
		});
	}
});

// Critère 2 (recherche de l'en-tête, Ctrl+K) : retiré en 03-03. L'en-tête unique (D-50) porte un
// lien « Rechercher » vers `/`, pas un second champ (hypothèse H7 du UI-SPEC, à confirmer par Ianis).

test.describe('Critère 3 — fiche commune : copier INSEE, fil d’Ariane, compteur', () => {
	test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

	test('le bouton Copier place le code INSEE dans le presse-papiers', async ({ page }) => {
		await mockAuthAnonymous(page);
		const source = await mockCommune(page);
		await page.goto('/commune/85047');
		await expect(page.getByRole('heading', { name: 'Challans', level: 1 })).toBeVisible({
			timeout: 10_000
		});
		await source.assert();

		const copyButton = page.getByRole('button', { name: 'Copier le code INSEE' });
		await expect(copyButton).not.toHaveAttribute('title');
		// Le titre est déjà présent dans le HTML SSR avant l'hydratation. Réessayer le clic
		// évite qu'une action trop précoce ne teste seulement le markup sans son handler Svelte.
		await expect(async () => {
			await copyButton.click();
			// 03-17 F9: the button keeps its text, the result is announced by a status region.
			await expect(page.locator('.fiche-sous').getByRole('status')).toHaveText('Code INSEE copié', { timeout: 500 });
		}).toPass({ timeout: 10_000 });
		const clipboard = await page.evaluate(() => navigator.clipboard.readText());
		expect(clipboard).toBe('85047');
	});

	// 03-07 (UI-SPEC « /commune/[code] » point 1): the breadcrumb is « Recherche / {Commune} ». The
	// departement stays one step away, as a filtered link, in the identity line of the page.
	for (const [nom, mockAuth] of [['anonyme', mockAuthAnonymous], ['authentifié', mockAuthLoggedIn]] as const) {
		test(`fil d’Ariane ${nom} : « Accueil » cliquable, la commune en page courante`, async ({ page }) => {
			await mockAuth(page);
			const source = await mockCommune(page);
			await page.goto('/commune/85047');

			const breadcrumb = page.getByRole('navigation', { name: "Fil d'Ariane" });
			await expect(breadcrumb).toBeVisible({ timeout: 10_000 });
			await expect(breadcrumb.getByRole('link', { name: 'Accueil' })).toHaveAttribute('href', '/');
			await expect(breadcrumb.getByText('Challans')).toHaveAttribute('aria-current', 'page');
			await expect(page.locator('.fiche-sous')).toContainText('Vendée (85) · INSEE 85047');
			await source.assert();
		});
	}

	// 03-08 (D-46): the global « {n} indicateurs · {m} sources » line left with the former themes;
	// 04.8-04 (UI-SPEC v4 § 6.2): the « N indicateurs » count of each folded theme left with the folding.
	// The invariant is kept on what replaced it: the rows of each theme, now placed in the categories of
	// the address fiche (4.9), equal the model of the data layer, computed on the very response the page
	// rendered; each row once, none outside a category.
	test('compteurs des thèmes égaux au modèle de la couche data', async ({ page }) => {
		await mockAuthAnonymous(page);
		const source = await mockCommune(page);
		const hydrated = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
		await page.goto('/commune/85047');
		await hydrated;
		await expect(page.getByRole('heading', { name: 'Challans', level: 1 })).toBeVisible({
			timeout: 10_000
		});

		// The expected figure is computed by the data layer itself (modules served by the dev
		// server), on the very response the page rendered — never a count written by hand.
		const attendu: { id: string; lignes: string[] }[] = await page.evaluate(async ({ commune, sources }) => {
			// Paths resolved by the dev server in the page, not by this file's type checker.
			const module = (nom: string) => `/src/lib/data/${nom}.ts`;
			const grid = await import(/* @vite-ignore */ module('commune-grid'));
			return grid.themesCommune(commune, sources).map((theme: { ancre: string; lignes: { id: string }[] }) => ({
				id: theme.ancre, lignes: theme.lignes.map((ligne) => ligne.id)
			}));
		}, { commune: MOCK_COMMUNE_85047.donnees[0], sources: MOCK_COMMUNE_85047.meta.source });
		expect(attendu.length, 'modèle vide : l’égalité serait vacuous').toBeGreaterThan(0);
		const rendues = await page.locator('main [data-ligne]').evaluateAll((nodes) =>
			nodes.filter((node) => !node.closest('.panneau-essentiel')).map((node) => ({ id: node.getAttribute('data-ligne')!, dansUneCategorie: Boolean(node.closest('details.cat dl.tab')) }))
		);
		expect(rendues.filter((ligne) => !ligne.dansUneCategorie), 'ligne hors catégorie').toEqual([]);
		for (const theme of attendu) {
			expect(theme.lignes.length, `${theme.id} : thème vide rendu`).toBeGreaterThan(0);
			for (const id of theme.lignes) expect(rendues.filter((ligne) => ligne.id === id), `${theme.id} : ligne ${id} une fois`).toHaveLength(1);
		}
		expect(rendues).toHaveLength(attendu.reduce((total, theme) => total + theme.lignes.length, 0));
		await source.assert();
	});
});
