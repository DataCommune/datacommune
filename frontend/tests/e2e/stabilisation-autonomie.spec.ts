import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { lireTheme } from './fixtures/themes';

// Synthetic adversarial communes captured from the real PostGIS -> ASGI tests. The captures predate
// D-47: the unknown count of 85991 is declared as the API serves it since 03-06 (not_reported). An
// undeclared absence would not be rendered at all (03-07 rule), which would prove nothing here.
const fixtures = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/commune-cor14-api.json', import.meta.url), 'utf8'));
const DECLARE: Record<string, Record<string, { etat: string; motif: string }>> = {
	'44991': {},
	'85991': { nb_equipements: { etat: 'not_reported', motif: 'Aucune ligne de la source pour cette commune.' } }
};
// 03-11 (D-40): the quarantine of rent control is served by `GET /v1/datasets` (declared in the
// backend catalogue) and shown on its card of « Données & méthode »; the former detail page
// `/datasets/encadrement-loyers` answers 308 towards that card.
const DATASETS_API = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8'));
function servi(code: string) {
	const reponse = structuredClone(fixtures[code]);
	reponse.donnees[0].etats = DECLARE[code];
	return reponse;
}

for (const width of [375, 1280]) {
	for (const code of ['44991', '85991']) {
		test(`stabilisation commune ${code}: zero/unknown, vintage, keyboard CSV and print ${width}px`, async ({ page }, testInfo) => {
			await page.setViewportSize({ width, height: 900 });
			await page.route('**/auth/me', route => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }));
			const probe = await configureSsrSource(page.request, [{
				method: 'GET', pathname: '/v1/commune', query: { code_insee: code },
				minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: servi(code) }]
			}]);
			const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
			await page.goto(`/commune/${code}`);
			await hydrated;
			// The facilities count is a row of the « Services & transports » theme, an open panel since
			// 04.8-04 (UI-SPEC v4 § 6.2): read in place, no theme link nor folding left to drive by keyboard.
			const environment = await lireTheme(page, 'services-transports');
			const row = environment.locator('.tab-l[data-field-id="equipements.nb_equipements"]');
			await expect(row.locator(':scope > dt')).toContainText('Effectif d’équipements observé');
			if (code === '44991') {
				// Zero measured: a value, with its vintage.
				await expect(row.locator('.tab-v .state-value')).toHaveText('0');
				await expect(row.locator('.tab-note-texte')).toHaveText('Millésime 2022');
				await expect(environment).toContainText('Équipements : millésime 2022');
			} else {
				// Unknown: its declared state, never a 0.
				await expect(row.locator('.tab-v .state-value')).toHaveCount(0);
				await expect(row.locator('.tab-v .state-absence .state-badge')).toHaveText('Non renseigné');
				await expect(row.locator('.tab-v .state-absence')).not.toContainText(/^0$/);
				await expect(environment).toContainText('Millésime des équipements inconnu');
			}
			await expect(environment).toContainText('sans garantie d’exhaustivité');
			// CSV at the keyboard: the export button, then the download button of its dialog.
			await page.getByRole('button', { name: 'Télécharger (CSV)' }).focus();
			await page.keyboard.press('Enter');
			const dialog = page.getByRole('dialog', { name: 'Exporter les données' });
			await expect(dialog).toBeVisible();
			await dialog.getByRole('button', { name: /^Télécharger \(\d+\)$/ }).focus();
			const downloading = page.waitForEvent('download');
			await page.keyboard.press('Enter');
			const download = await downloading;
			const csv = readFileSync((await download.path())!, 'utf8');
			expect(csv).toContain('Une donnée absente ne vaut pas zéro.');
			if (code === '44991') expect(csv).toContain('Effectif d’équipements observé,0');
			else expect(csv).not.toContain('Effectif d’équipements observé,0');
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
			await page.screenshot({ path: testInfo.outputPath(`commune-${code}-${width}.png`), fullPage: true });
			await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
			await page.emulateMedia({ media: 'print' });
			await expect(environment.getByText(/Une donnée absente ne vaut pas zéro/)).toBeVisible();
			await probe.assert();
		});
	}

	test(`stabilisation rent control quarantine is visible in rendered catalog ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await page.route('**/auth/me', route => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }));
		const probe = await configureSsrSource(page.request, [{
			method: 'GET', pathname: '/v1/datasets', query: {},
			minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: DATASETS_API }]
		}]);
		const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
		await page.goto('/datasets/encadrement-loyers');
		await hydrated;
		// Since 04.5-11 (B7) the former page leads to the detail of its source, its row targeted.
		await expect(page).toHaveURL(/\/datasets\?jeu=encadrement_loyers#encadrement_loyers$/);
		const ligne = page.locator('article#encadrement_loyers');
		await expect(ligne).toBeInViewport();
		await expect(ligne.locator('.dataset-badge-warn')).toHaveText('Non qualifié');
		const detail = page.locator('main .dataset-detail');
		await expect(detail.locator('.dataset-badge-warn')).toHaveText('Non qualifié');
		await expect(detail).toContainText('Service indisponible');
		await expect(detail).toContainText('HTTP 503');
		await expect(detail).toContainText('sélection manuelle');
		await expect(page.locator('main')).not.toContainText('Graslin');
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
		await probe.assert();
	});
}
