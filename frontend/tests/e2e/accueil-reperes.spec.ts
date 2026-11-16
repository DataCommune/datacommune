import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, type TestInfo } from '@playwright/test';
import { REPERES_NATIONAUX } from '../../src/lib/data/accueil';
import { verifierPlancherTypo } from './fixtures/quality';

// Home page beyond the search (04.6-01): the national landmarks, the publics and the method hold at
// 1440, 375 and 320 px — no horizontal overflow, whole sources, 44 px targets, no axe violation — and
// the search stays the first thing on the page. The figures are static official quotes: the page
// still sends no /v1 request (marketing-truth.spec.ts keeps that guard).

function evidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_HOME_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

for (const width of [1440, 375, 320] as const) {
	test(`accueil : repères, publics et méthode lisibles à ${width} px`, async ({ page }, testInfo) => {
		await page.route('**/auth/me', (route) =>
			route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Non authentifié"}' })
		);
		await page.setViewportSize({ width, height: 900 });
		// The « i » of a landmark opens once the page is hydrated: the account is read then.
		const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
		await page.goto('/');
		await hydratee;
		await page.evaluate(() => document.fonts.ready);

		const main = page.getByRole('main');
		// 04.8-07 (UI-SPEC § 3.13, § 6.6): the large field of the v4 home.
		const search = main.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
		const reperes = main.getByRole('region', { name: 'Repères nationaux' });
		await expect(search).toBeVisible();
		await expect(reperes).toBeVisible();
		const [searchBox, reperesBox] = await Promise.all([search.boundingBox(), reperes.boundingBox()]);
		expect(searchBox!.y, 'la recherche précède les repères').toBeLessThan(reperesBox!.y);

		// 04.8-07 (UI-SPEC § 6.6): each landmark is a tile `div.cle` of `dl.home-reperes` (was a list item).
		const items = reperes.locator('dl.home-reperes > .cle');
		await expect(items).toHaveCount(REPERES_NATIONAUX.length);
		for (const [index, repere] of REPERES_NATIONAUX.entries()) {
			const item = items.nth(index);
			await expect(item).toContainText(repere.valeur);
			await expect(item).toContainText(repere.portee);
			// The publication is behind the « i » of the tile, one action away.
			await expect(item.getByRole('link', { name: repere.source })).toBeHidden();
			await item.getByRole('button', { name: /^Source et définition/ }).click();
			const panneau = page.getByRole('dialog');
			await expect(panneau.getByRole('link', { name: repere.source })).toHaveAttribute('href', repere.url);
			await panneau.getByRole('button', { name: 'Fermer' }).click();
		}

		for (const name of ['Pour commencer', 'D’où viennent les chiffres']) {
			await expect(main.getByRole('heading', { level: 2, name })).toBeVisible();
		}
		for (const link of await main.locator('.pros a, .home-method-links a').all()) {
			expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
		}

		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
		expect(overflow, `aucun débordement horizontal à ${width} px`).toBeLessThanOrEqual(0);
		// 04.8-01 (D-03): no visible text under 14 px.
		expect(await verifierPlancherTypo(page, '.public-site'), `plancher de 14 px à ${width} px`).toEqual([]);

		const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
		expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);

		await page.screenshot({ path: evidencePath(testInfo, `accueil-complet-${width}.png`), fullPage: true, animations: 'disabled' });
		// Paper copy of the page (A4), kept as evidence of the print rules: a landmark is not split.
		if (width === 1440) await page.pdf({ path: evidencePath(testInfo, 'accueil.pdf'), format: 'A4' });
	});
}
