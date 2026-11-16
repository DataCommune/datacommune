import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { lireTheme } from './fixtures/themes';
import { monterVolet } from './fixtures/volets';

// These are HTTP captures from disposable SQL integration tests, not live measurements.
// Income uses a minimal materialized projection; rents use the actual commune rebuild.
const evidence = new URL('../../../docs/audit-stabilisation-2026-09-08/lot-02/', import.meta.url);
const income = JSON.parse(readFileSync(new URL('cor05-api-fixture.json', evidence), 'utf8'));
const rents = JSON.parse(readFileSync(new URL('cor06-api-fixture.json', evidence), 'utf8'));

async function showCommune(page: Page, code: string, payload: unknown) {
	await page.route('**/auth/me', route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Anonymous fixture' }) }));
	const probe = await configureSsrSource(page.request, [{
		method: 'GET', pathname: '/v1/commune', query: { code_insee: code },
		minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: payload }]
	}]);
	// auth.init runs from the root onMount: wait for hydration before clicking SSR controls.
	const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
	await page.goto(`/commune/${code}`);
	await hydrated;
	// 4.9 lot 3: the panel is always there, its strip only when a marker is known.
	await expect(page.locator('#essentiel')).toBeVisible();
	return probe;
}

/** Reads a theme of the fiche from its link (04.5-02, D45-07) and returns it. */
function openTheme(page: Page, id: string) {
	return lireTheme(page, id);
}

for (const width of [375, 1280]) {
	test(`L02 income source capture keeps declared meaning, with no browser-computed benchmark, at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		const probe = await showCommune(page, income.oracle.code_insee, income.payload);
		// 03-07 (D-46, E1): the income left the synthesis; its row of the folded « Contexte communal »
		// theme keeps the declared meaning, under the value and in its source panel. The comparison
		// with a national referential is not displayed: the API does not serve that reference, and
		// the browser never computes it (03-08, deferred in champs-maquette-hors-synthese.md).
		const theme = await openTheme(page, 'contexte-communal');
		const row = theme.locator('.tab-l[data-field-id="revenus.revenu_median"]');
		const valeur = row.locator('.tab-v .state-value');
		await expect(valeur.locator('.value')).toHaveText(/^24\s380$/);
		await expect(valeur.locator('.unit')).toContainText('€/UC/an');
		// The caveat is read in the source panel of the row (« Limites », 03-17 R-1).
		await monterVolet(row);
		const limites = row.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Limites$/ }) }).locator('dd');
		await expect(limites).toContainText('déclaré');
		await expect(limites).toContainText('avant redistribution');
		await row.getByRole('button', { name: /^Détail : / }).click();
		const volet = page.locator('dialog.source-panel[open]');
		await expect(volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Définition$/ }) }).locator('dd'))
			.toContainText('avant redistribution');
		await page.keyboard.press('Escape');
		await expect(page.getByText(/22\s900/)).toHaveCount(0);
		await probe.assert();
	});

	test(`L02 rent source capture preserves mesh, zero observations and export context at ${width}px`, async ({ page }, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		const probe = await showCommune(page, '05066', rents.communes['05066']);
		const market = await openTheme(page, 'immobilier');
		await expect(market).toContainText("Loyer d'annonce prédit appartement");
		await expect(market).toContainText('T3 2025');
		await expect(market).toContainText('charges comprises');
		await expect(market).toContainText('0 obs. commune / 484 obs. maille');
		await expect(market).toContainText('R² ajusté');
		await expect(market).not.toContainText('Loyer médian');
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
		await page.screenshot({ path: testInfo.outputPath(`loyers-${width}.png`), fullPage: true });
		// Export of the category « Prix et ventes » alone, from the export dialog of the fiche (C1, arbitrated by
		// Ianis on 2026-11-10: the CSV follows the categories of the page; the rents were under « Immobilier »).
		const downloadPromise = page.waitForEvent('download');
		await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
		const dialog = page.getByRole('dialog', { name: 'Exporter les données' });
		await dialog.getByRole('button', { name: 'Tout décocher' }).click();
		await dialog.getByRole('checkbox', { name: 'Prix et ventes' }).check();
		await dialog.getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
		const download = await downloadPromise;
		const path = await download.path();
		expect(path).not.toBeNull();
		const csv = readFileSync(path!, 'utf8');
		expect(csv).toContain('charges comprises');
		expect(csv).toContain('0 obs. commune / 484 obs. maille');
		expect(csv.split(/\r?\n/).slice(1).filter(Boolean).every((ligne) => ligne.startsWith('Prix et ventes,'))).toBe(true);
		await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
		await page.emulateMedia({ media: 'print' });
		await expect(market).toContainText('T3 2025');
		const rent = market.locator('.tab-l[data-field-id="loyers.loyer_m2_appartement"]');
		// On paper the panels are not printed: the caveat stays under the value (03-17 R-1, SEM-01).
		await expect(rent.locator('.tab-reserve').getByText(/0 obs\. commune \/ 484 obs\. maille/)).toBeVisible();
		await probe.assert();
	});

	test(`L02 legacy rent retains an explicit unknown context at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		// Explicitly synthetic legacy variant; it is not labelled as a SQL capture.
		const legacy = structuredClone(rents.communes['05066']);
		legacy.donnees[0].loyers_appartement = null;
		const probe = await showCommune(page, '05066', legacy);
		const market = await openTheme(page, 'immobilier');
		await expect(market).toContainText('Précisions inconnues');
		await expect(market).not.toContainText('484 obs. maille');
		await expect(market).not.toContainText('Réimport source requis');
		await probe.assert();
	});
}
