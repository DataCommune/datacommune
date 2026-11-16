// Error report switched on (framing of 2026-11-10). NOT part of the ordinary pass: the shared dev
// server of the specs runs with the report off, as every build does by default. Run apart, with the
// switch in the environment the dev server inherits (playwright.config.ts spreads process.env):
//
//   $env:VITE_SIGNALEMENT = 'true'; pwsh -NoProfile -File scripts/test-e2e.ps1 -Specs tests/e2e/signalement.spec.ts
//
// What it shows: both entries (information panel, footer), the context displayed before sending, the
// keyboard path and where the focus goes after each answer, the same path without JavaScript, the
// worded failures, and that the page neither sets a cookie nor overflows a narrow screen. The API is
// the fixture server: what the site sends in the body is covered by `page.server.test.ts`.
// Wordings are read from the module to validate: the spec follows the owner's words when they change.
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { ADRESSE_SIGNALEMENT, LIBELLES } from '../../src/lib/signalement/libelles';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './fixtures/commune-indicators';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

const REFERENCE = '0A1B2C3D4E';
const MESSAGE = 'Le prix médian des maisons paraît dix fois trop bas.';

function attente(method: string, pathname: string, query: Record<string, string>, reponse: { status: number; body: unknown; headers?: Record<string, string> }) {
	return { method, pathname, query, minCalls: 0, maxCalls: 20, responses: Array.from({ length: 20 }, () => reponse) };
}

async function configurer(request: APIRequestContext, depot: { status: number; body: unknown; headers?: Record<string, string> }) {
	const commune = { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } };
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				attente('GET', '/v1/commune', { code_insee: '85191' }, { status: 200, body: commune }),
				attente('POST', '/v1/signalements', {}, depot)
			]
		}
	});
	expect(res.ok()).toBe(true);
}

const RECU = { status: 201, body: { reference: REFERENCE } };

async function depots(request: APIRequestContext): Promise<{ method: string; pathname: string }[]> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	const { requests, violations } = (await res.json()) as { requests: { method: string; pathname: string }[]; violations: unknown[] };
	expect(violations).toEqual([]);
	return requests.filter((r) => r.pathname === '/v1/signalements');
}

/** The context list of the form, as term → value. */
async function joint(page: Page): Promise<Record<string, string>> {
	return page.locator('.signaler-joint > div').evaluateAll((lignes) =>
		Object.fromEntries(
			lignes.map((ligne) => [
				(ligne.querySelector('dt')?.textContent ?? '').trim(),
				(ligne.querySelector('dd')?.textContent ?? '').replace(/\s+/g, ' ').trim()
			])
		)
	);
}

test.beforeAll(async ({ request }) => {
	const page = await request.get('/signaler');
	if (page.status() === 404) {
		throw new Error('signalement.spec.ts needs the dev server started with VITE_SIGNALEMENT=true (see the head of the file)');
	}
});

test('depuis le volet d’une valeur : contexte montré, parcours au clavier, confirmation et retour', async ({ page, request }) => {
	await configurer(request, RECU);
	// The « i » opens once the page is hydrated: the session is read then.
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto('/commune/85191');
	await hydratee;

	// Entry: the last control of the information panel, never the face of the row.
	const bouton = page.getByRole('button', { name: /^(Détail|Source et définition) : / }).first();
	await bouton.click();
	const volet = page.locator('dialog[open]');
	const titreVolet = (await volet.getByRole('heading', { level: 2 }).textContent())!.trim();
	const lien = volet.getByRole('link', { name: LIBELLES.volet });
	await expect(lien).toBeVisible();
	expect(await volet.locator('a[href], button, summary').last().evaluate((dernier, cible) => dernier === cible, await lien.elementHandle())).toBe(true);
	await lien.focus();
	await page.keyboard.press('Enter');

	await expect(page).toHaveURL(/\/signaler\?/);
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(LIBELLES.titre);
	await expect(page).toHaveTitle(LIBELLES.onglet);
	const contexte = await joint(page);
	expect(contexte[LIBELLES.termes.page]).toBe('/commune/85191');
	expect(contexte[LIBELLES.termes.valeur]).toContain(titreVolet);
	expect(contexte[LIBELLES.termes.version]).not.toBe('');
	expect(contexte[LIBELLES.termes.largeur]).toMatch(/^\d+ px$/);
	expect(contexte[LIBELLES.termes.navigateur]).toContain('Mozilla');

	// Keyboard: message, e-mail address, button; the honeypot is never a stop.
	const message = page.getByLabel(LIBELLES.commentaire);
	const email = page.getByLabel(LIBELLES.email);
	const envoyer = page.getByRole('button', { name: LIBELLES.envoyer });
	await message.focus();
	await page.keyboard.press('Tab');
	await expect(email).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(envoyer).toBeFocused();

	// A refusal: worded next to its field, which takes the focus and keeps what was typed.
	await message.fill('court');
	await envoyer.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByText(LIBELLES.erreurs.court)).toBeVisible();
	await expect(message).toBeFocused();
	await expect(message).toHaveAttribute('aria-invalid', 'true');
	await expect(message).toHaveValue('court');
	expect(await depots(request)).toHaveLength(0);

	// Sent: the confirmation replaces the form and takes the focus; the way back is the page left.
	await message.fill(MESSAGE);
	await envoyer.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(LIBELLES.recu);
	const confirmation = page.locator('.signaler-recu');
	await expect(confirmation).toContainText(REFERENCE);
	await expect(confirmation).toBeFocused();
	await expect(page.getByRole('button', { name: LIBELLES.envoyer })).toHaveCount(0);
	expect(await depots(request)).toEqual([expect.objectContaining({ method: 'POST', pathname: '/v1/signalements' })]);
	await page.getByRole('link', { name: LIBELLES.retour }).click();
	await expect(page).toHaveURL(/\/commune\/85191$/);
});

test('sans JavaScript, depuis le pied : la page d’origine est jointe, le refus et la confirmation sont rendus par le serveur', async ({ browser, request }) => {
	await configurer(request, RECU);
	const contexteSansScript = await browser.newContext({ javaScriptEnabled: false });
	const page = await contexteSansScript.newPage();
	try {
		await page.goto('/commune/85191');
		const lien = page.getByRole('contentinfo').getByRole('link', { name: LIBELLES.pied });
		await expect(lien).toHaveAttribute('href', '/signaler');
		await lien.click();

		await expect(page.getByRole('heading', { level: 1 })).toHaveText(LIBELLES.titre);
		const contexte = await joint(page);
		expect(contexte[LIBELLES.termes.page]).toBe('/commune/85191');
		// Known in the browser only: not shown, not sent.
		expect(contexte[LIBELLES.termes.largeur]).toBeUndefined();

		await page.getByLabel(LIBELLES.commentaire).fill('court');
		await page.getByLabel(LIBELLES.email).fill('pas-un-email');
		await page.getByRole('button', { name: LIBELLES.envoyer }).click();
		await expect(page.getByText(LIBELLES.erreurs.court)).toBeVisible();
		await expect(page.getByText(LIBELLES.erreurs.invalide)).toBeVisible();
		await expect(page.getByLabel(LIBELLES.commentaire)).toHaveValue('court');
		await expect(page.getByLabel(LIBELLES.email)).toHaveValue('pas-un-email');
		expect((await joint(page))[LIBELLES.termes.page]).toBe('/commune/85191');
		expect(await depots(request)).toHaveLength(0);

		await page.getByLabel(LIBELLES.commentaire).fill(MESSAGE);
		await page.getByLabel(LIBELLES.email).fill('');
		await page.getByRole('button', { name: LIBELLES.envoyer }).click();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(LIBELLES.recu);
		await expect(page.locator('.signaler-recu')).toContainText(REFERENCE);
		await expect(page.getByRole('link', { name: LIBELLES.retour })).toHaveAttribute('href', '/commune/85191');
		expect(await depots(request)).toHaveLength(1);
	} finally {
		await contexteSansScript.close();
	}
});

for (const [nom, depot, libelle] of [
	['un refus de débit', { status: 429, body: { title: 'Trop de signalements', status: 429 }, headers: { 'retry-after': '600' } }, LIBELLES.pannes.debit],
	['une panne de l’API', { status: 503, body: { title: 'Signalement momentanement indisponible', status: 503 } }, LIBELLES.pannes.envoi]
] as const) {
	test(`${nom} est annoncé avec l’adresse où écrire, et le message reste dans le champ`, async ({ page, request }) => {
		await configurer(request, depot);
		// Typed at once, hydrated or not: what is typed while the page loads is kept.
		await page.goto('/signaler');
		await page.getByLabel(LIBELLES.commentaire).fill(MESSAGE);
		await page.getByRole('button', { name: LIBELLES.envoyer }).click();

		const alerte = page.getByRole('alert');
		await expect(alerte).toHaveText(`${libelle}${ADRESSE_SIGNALEMENT}.`);
		await expect(alerte).toBeFocused();
		await expect(alerte.getByRole('link')).toHaveAttribute('href', `mailto:${ADRESSE_SIGNALEMENT}`);
		await expect(page.getByLabel(LIBELLES.commentaire)).toHaveValue(MESSAGE);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(LIBELLES.titre);
	});
}

test('la page n’est ni indexée ni mise en cache, ne dépose aucun cookie et tient dans 320 px', async ({ page, request, context }) => {
	await configurer(request, RECU);
	await page.setViewportSize({ width: 320, height: 640 });
	const reponse = await page.goto('/signaler?de=%2Fcommune%2F85191&titre=Prix&lignes=a%3D1&source=DVF');

	expect(reponse?.headers()['x-robots-tag']).toBe('noindex, nofollow');
	expect(reponse?.headers()['cache-control']).toBe('no-store');
	await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
	expect(await context.cookies()).toEqual([]);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
	// The honeypot is out of the accessibility tree and off the page: two text fields, not three.
	await expect(page.getByRole('main').getByRole('textbox')).toHaveCount(2); // message and e-mail address
	await expect(page.locator('input[name="site_web"]')).not.toBeInViewport();
	// Only a path of the site is ever joined: another origin in the address bar is dropped.
	await page.goto('/signaler?de=https%3A%2F%2Failleurs.example%2F');
	expect((await joint(page))[LIBELLES.termes.page]).toBeUndefined();
});
