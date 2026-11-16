import { test, expect } from '@playwright/test';

// Coquille app v3.0 — hub + 404 legacy + nav sitemap (SHELL-01 / SHELL-02 / SHELL-03).
//
// Aucune dépendance à l'API DataCommune : seul /auth/me est mocké pour franchir le guard
// du route group (app). Cette suite DOIT passer entièrement même API down (le 530 ne la
// concerne pas), contrairement au smoke live de fiche-commune.spec.ts.
//
// Note 404 en SPA (ssr:false) : une route absente fait rendre le +error.svelte racine
// côté client avec $page.status === 404. Les assertions portent sur le RENDU du 404
// (code « 404 » + message « Cette section a évolué » + lien Mon espace, ADR D-09), pas
// sur response.status() : c'est ce qui rend la suite indépendante de l'hébergeur.
// Le statut HTTP du document était un 200 de repli servi par le Worker Cloudflare.
// L'adaptateur est désormais adapter-node (plan 01-06) et ce statut n'est PAS
// reverifie ici : cette spec est classée `legacy` dans e2e-manifest.json, la gate ne
// l'exécute pas. À rejouer quand la cible d'hébergement sera choisie (phase 5).
// Aucun redirect : l'URL demandée reste affichée (preuve no-redirect).

const MOCK_USER = {
	id: 2,
	email: 'test@datacommune.fr',
	name: 'Test User',
	is_verified: true,
	is_admin: false,
	created_at: '2026-01-01T00:00:00Z'
};

const LEGACY_ROUTES = ['/workspace', '/explore', '/admin/chat'];

async function mockAuth(page: import('@playwright/test').Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) })
	);
}

test.describe('SHELL-01 — Hub « Mon espace » (zéro jargon « dataset »)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	test('/mon-espace montre 3 cartes + champ d’accès rapide commune', async ({ page }) => {
		await page.goto('/mon-espace');

		// Titre du hub rendu (guard franchi).
		await expect(page.getByRole('heading', { name: 'Mon espace', level: 1 })).toBeVisible({
			timeout: 10_000
		});

		// 3 cartes d'entrée : Fiche Commune (active) + Bien / Prospection (« bientôt »).
		await expect(page.getByRole('heading', { name: 'Fiche Commune', level: 2 })).toBeVisible();
		await expect(page.getByRole('heading', { name: 'Fiche Bien', level: 2 })).toBeVisible();
		await expect(page.getByRole('heading', { name: 'Prospection', level: 2 })).toBeVisible();

		// Champ d'accès rapide commune présent.
		await expect(page.getByPlaceholder('Rechercher une commune...')).toBeVisible();
	});

	test('le hub ne contient AUCUN jargon « dataset »', async ({ page }) => {
		await page.goto('/mon-espace');
		await expect(page.getByRole('heading', { name: 'Mon espace', level: 1 })).toBeVisible({
			timeout: 10_000
		});

		// Zéro occurrence du mot « dataset » (insensible à la casse) dans la page rendue.
		await expect(page.locator('body')).not.toContainText(/dataset/i);
	});
});

test.describe('SHELL-02 — Routes legacy → 404 propre (no-redirect)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	for (const route of LEGACY_ROUTES) {
		test(`${route} rend un 404 propre + lien Mon espace, sans redirect`, async ({ page }) => {
			await page.goto(route);

			// Rendu du 404 : code « 404 » + message « Cette section a évolué » (ADR D-09).
			await expect(page.getByRole('heading', { name: '404', level: 1 })).toBeVisible({
				timeout: 10_000
			});
			await expect(page.getByText('Cette section a évolué')).toBeVisible();

			// Lien vers le hub (récupération maîtrisée, pas un cul-de-sac).
			const monEspaceLink = page.getByRole('link', { name: /mon espace/i });
			await expect(monEspaceLink).toBeVisible();
			await expect(monEspaceLink).toHaveAttribute('href', '/mon-espace');

			// No-redirect : l'URL reste celle demandée (pas de 301/302 vers /mon-espace).
			expect(new URL(page.url()).pathname).toBe(route);
		});
	}
});

test.describe('SHELL-03 — Nav app reflète le sitemap ADR', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuth(page);
	});

	test('la nav porte les libellés du sitemap et plus aucun libellé legacy', async ({ page }) => {
		await page.goto('/mon-espace');
		await expect(page.getByRole('heading', { name: 'Mon espace', level: 1 })).toBeVisible({
			timeout: 10_000
		});

		const nav = page.locator('header nav');
		await expect(nav).toBeVisible();

		// Libellés du sitemap v3.0 présents dans la nav. Les libellés actifs sont des
		// <a>, Fiche Bien / Prospection sont des <span> inactifs (D9-05, pas de lien
		// mort) — on asserte donc la PRÉSENCE du texte dans la nav, sans présumer du tag.
		// (toContainText, pas getByText exact : chaque libellé cohabite avec son icône
		// dans le même élément, ce qui casse un match « exact ».)
		for (const label of ['Mon espace', 'Fiche Commune', 'Fiche Bien', 'Prospection', 'Compte']) {
			await expect(nav).toContainText(label);
		}

		// Plus aucun libellé de l'explorateur générique legacy.
		await expect(nav).not.toContainText(/explorer/i);
		await expect(nav).not.toContainText(/workspace/i);
	});
});
