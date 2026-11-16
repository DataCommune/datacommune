import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { test, expect, chromium, type Page } from '@playwright/test';
import { CODE_INSEE, mockAuthAnonyme, mockCommune } from './fixtures/mocks-matrice';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { installLocalNetworkGuard, expectNoUnexpectedOffLocalRequest } from './fixtures/local-network';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { COMMUNE_META_SOURCE_NOMS, COMMUNE_META_SOURCE_ORDER } from '../../src/lib/data/explications';
import { COMMUNE_FLAT_85047, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';
import { BAN_ID, sourceAdresse } from './fixtures/surfaces-phase4';
import { lireTheme } from './fixtures/themes';

/** The five theme panels of the commune fiche, in the mock-up order (D-46, UI-SPEC v4 § 6.2). */
// The categories of the commune fiche since 4.9, in their order: they hold the rows of the five themes.
const CATEGORIES = ['q-prix', 'q-logement', 'q-quotidien'];

// Synthetic transport fixtures. These assertions qualify the real SSR/page/CSV path,
// not source accuracy, dwelling identity or the production deployment.
//
// 03-07: the commune synthesis is the KPI strip (D-46) with its source panel in a <dialog>. 04-08:
// the Fiche Bien left with `/bien` (D-101, a 308); its surface here is the address page that
// replaced it, read by its identity and the headings of its lists since 04.7-03. The invariants stay per surface: the
// server-rendered synthesis equals the hydrated one (no JavaScript needed to read it), it is
// usable at the keyboard, it prints, and it holds at 320/375/1280px and at a real 200 % zoom. The
// commune income witness moved with the income itself: to its theme row (E1), and since 03-08 that
// row is a row of the « Contexte communal » theme (D-46), an open panel since 04.8-04.
test.setTimeout(60_000);
const surfaces = [
	{ name: 'commune', path: `/commune/${CODE_INSEE}`, configure: mockCommune },
	{ name: 'adresse', path: `/adresse/${BAN_ID}`, configure: (page: Page) => configureSsrSource(page.request, sourceAdresse()) }
] as const;

async function configure(page: Page, surface: typeof surfaces[number]) {
	await installLocalNetworkGuard(page);
	await mockAuthAnonyme(page);
	return surface.configure(page);
}

/** Commune: the KPI strip, as rendered (ids, labels, values, states, metadata). The source of a
 *  KPI is read in its source panel (03-17 R-1), rendered with the page: SSR, hydrated and printed
 *  snapshots must name the same source (03-17 D-15 M3). */
async function kpiSnapshot(page: Page) {
	const strip = page.locator('.kpi-strip');
	await expect(strip).toBeVisible();
	return strip.locator('[data-kpi]').evaluateAll((kpis) => kpis.map((kpi) => ({
		kpi: kpi.getAttribute('data-kpi'),
		label: kpi.querySelector('dt.kpi-label')?.textContent,
		lines: [...kpi.querySelectorAll('.kpi-line')].map((line) => ({
			field: line.getAttribute('data-field-id'),
			state: line.querySelector('[data-state]')?.getAttribute('data-state') ?? null,
			text: line.textContent?.replace(/\s+/g, ' ').trim()
		})),
		source: [...kpi.querySelectorAll('.source-panel-entry')]
			.find((entry) => entry.querySelector('dt')?.textContent?.trim() === 'Source')
			?.querySelector('dd')?.textContent?.trim() ?? null
	})));
}

/** Address (04.7-03): its identity and the headings of its lists, as rendered. */
async function repereSnapshot(page: Page) {
	const reperes = page.locator('h1, .adresse-identity, section.doclist h3');
	await expect(reperes.first()).toBeVisible();
	return reperes.evaluateAll((nodes) => nodes.map((node) => ({
		kpi: node.tagName.toLowerCase(),
		text: node.textContent?.replace(/\s+/g, ' ').trim()
	})));
}

function snapshot(page: Page, surface: typeof surfaces[number]) {
	return surface.name === 'commune' ? kpiSnapshot(page) : repereSnapshot(page);
}

for (const width of [320, 375, 1280]) {
	for (const surface of surfaces) {
		test(`grid ${surface.name}: SSR, keyboard, scope and print ${width}px`, async ({ browser, page }, testInfo) => {
			const noJs = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width, height: 900 } });
			const ssr = await noJs.newPage();
			let before;
			try {
				const probe = await configure(ssr, surface);
				await ssr.goto(surface.path);
				before = await snapshot(ssr, surface);
				await probe.assert();
				expectNoUnexpectedOffLocalRequest(ssr);
			} finally {
				await noJs.close();
			}
			await page.setViewportSize({ width, height: 900 });
			const probe = await configure(page, surface);
			const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
			await page.goto(surface.path);
			await hydrated;
			writeFileSync(testInfo.outputPath('source-audit-hydrated.json'), await (await page.request.get(`${E2E_SOURCE_ORIGIN}/__requests`)).text());
			expect(await snapshot(page, surface)).toEqual(before);

			if (surface.name === 'commune') {
				expect(before.map((kpi) => (kpi as { kpi: string }).kpi)).toEqual(['prix-maisons', 'dpe-dominant']);
				// The compared source is the served one, not a field that is always null (M3).
				expect((before[0] as { source: string | null }).source).toBe(COMMUNE_META_SOURCE_NOMS.dvf);
				const maisons = page.locator('[data-kpi="prix-maisons"]');
				await expect(maisons.locator('.kpi-line .value')).toHaveText('2\u202f480');
				// The source is read in the panel, not on the card (03-17 R-1).
				await expect(maisons.locator('.kpi-source')).toHaveCount(0);
				await expect(maisons.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Source$/ }) }).locator('dd'))
					.toHaveText(COMMUNE_META_SOURCE_NOMS.dvf);
				// Keyboard: the i button opens the source panel, Escape closes it, focus comes back.
				const bouton = maisons.locator('.source-button');
				await bouton.focus();
				await page.keyboard.press('Enter');
				const volet = page.locator('dialog.source-panel[open]');
				await expect(volet).toBeVisible();
				await page.keyboard.press('Tab');
				await page.keyboard.press('Escape');
				await expect(volet).toHaveCount(0);
				await expect(bouton).toBeFocused();
			} else {
				// The address page: its identity, the documents section, never indexed.
				expect(before.length).toBe(3);
				await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
				await expect(page.getByRole('heading', { level: 3, name: /documents DPE déclarés à cette adresse/i })).toBeVisible();
				await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/);
			}

			if (surface.name === 'commune' && width === 375) {
				// The income left the synthesis (E1): its witness reads its row of the « Contexte
				// communal » theme, with the same value, source, vintage, scope and visible reserve, and
				// still rejects corruption. The theme is read from its panel (04.8-04).
				const theme = await lireTheme(page, 'contexte-communal');
				const income = theme.locator('.tab-l[data-field-id="revenus.revenu_median"]');
				// 03-17 R-1: source, object and caveats of the row are read in its source panel. The
				// caveat is visible to the reader once the panel is opened by the row's i button:
				// `checkVisibility()`, not the computed display of a <dd> inside a closed <dialog>,
				// which is always `block` (03-17 D-15 M2).
				const readWitness = () => income.evaluate(row => {
					const text = (selector: string) => row.querySelector(selector)?.textContent?.replace(/\s+/g, ' ').trim();
					const entree = (terme: string) => [...row.querySelectorAll('.source-panel-entry')]
						.find((node) => node.querySelector('dt')?.textContent === terme)?.querySelector('dd') ?? null;
					const lu = (terme: string) => entree(terme)?.textContent?.replace(/\s+/g, ' ').trim();
					const reserve = entree('Limites')!;
					return {
						value: text('.tab-v .state-value'), source: lu('Source'), period: text('.tab-note-texte'),
						grain: lu('Objet'), reserve: lu('Limites'), reserveVisible: reserve.checkVisibility()
					};
				});
				const voletRevenu = page.locator('dialog.source-panel[open]');
				const entreeVolet = (terme: string) =>
					income.locator('.source-panel-entry', { has: page.locator('dt', { hasText: new RegExp(`^${terme}$`) }) }).locator('dd');
				const assertWitness = (witness: Awaited<ReturnType<typeof readWitness>>) => {
					expect(witness.value).toBe('21 340 €/UC/an');
					expect(witness.source).toBe(COMMUNE_META_SOURCE_NOMS.revenus);
					expect(witness.period).toBe('Millésime 2021');
					expect(witness.grain).toBe('Commune');
					expect(witness.reserve).toContain('avant redistribution');
					expect(witness.reserveVisible).toBe(true);
				};
				// Panel closed: the caveat is not in front of the reader, the witness refuses it.
				const ferme = await readWitness();
				expect(() => assertWitness(ferme)).toThrow();
				await income.getByRole('button', { name: /^Détail : / }).click();
				await expect(voletRevenu).toBeVisible();
				await expect(voletRevenu.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Limites$/ }) }).locator('dd')).toBeVisible();
				assertWitness(await readWitness());
				const mutations = [
					income.locator('.tab-v .state-value .value'), entreeVolet('Source'),
					income.locator('.tab-note-texte'), entreeVolet('Objet'), entreeVolet('Limites')
				];
				for (const target of mutations) {
					const handle = await target.elementHandle();
					if (!handle) throw new Error('Missing mutation target');
					const original = await handle.evaluate(el => {
						const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
						const original: string[] = [];
						while (walker.nextNode()) { original.push(walker.currentNode.textContent ?? ''); walker.currentNode.textContent = 'Corruption synthétique'; }
						return original;
					});
					const corrupt = await readWitness();
					expect(() => assertWitness(corrupt)).toThrow();
					await handle.evaluate((el, original) => {
						const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let index = 0;
						while (walker.nextNode()) walker.currentNode.textContent = original[index++];
					}, original);
					await handle.dispose();
				}
				const reserve = entreeVolet('Limites');
				await reserve.evaluate(el => { (el as HTMLElement).style.display = 'none'; });
				const hidden = await readWitness();
				expect(() => assertWitness(hidden)).toThrow();
				await reserve.evaluate(el => { (el as HTMLElement).style.display = ''; });
				assertWitness(await readWitness());
				await page.keyboard.press('Escape');
				await expect(voletRevenu).toHaveCount(0);
			}

			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
			await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
			await page.screenshot({ path: testInfo.outputPath(`${surface.name}-${width}.png`), fullPage: true });
			await page.screenshot({ path: testInfo.outputPath(`${surface.name}-${width}-viewport.png`) });
			// Printing: the print media applies; every category is open on paper, even a fold closed on screen (4.9).
			await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
			await page.emulateMedia({ media: 'print' });
			for (const cell of await page.locator('table.fiche-grid td').all()) {
				await expect(cell).toHaveCSS('padding-left', '16px');
			}
			expect(await snapshot(page, surface)).toEqual(before);
			await expect(page.locator('.skip-link')).toBeHidden();
			if (surface.name === 'commune') {
				await expect(page.locator('.kpi-strip')).toBeVisible();
				await expect(page.locator('.source-button').first()).toBeHidden();
				const categories = page.locator('details.cat:has(dl.tab)');
				expect(await categories.evaluateAll((nodes) => nodes.map((node) => node.id))).toEqual(CATEGORIES);
				for (const categorie of await categories.all()) {
					await expect(categorie).toHaveJSProperty('open', true);
					await expect(categorie.locator('dl.tab > .tab-l').first()).toBeVisible();
				}
			}
			if (width !== 320) {
				const pdf = await page.pdf({ path: testInfo.outputPath(`${surface.name}-${width}.pdf`), format: 'A4' });
				if (surface.name === 'commune' && width === 375) {
					// Every category open, the commune sheet still prints on 12 A4 pages at most.
					const pageCount = pdf.toString('latin1').match(/\/Type\s*\/Page\b/g)?.length ?? 0;
					expect(pageCount).toBeGreaterThan(0);
					expect(pageCount).toBeLessThanOrEqual(12);
					// The commune rows are `dl.tab` rows since 04.8-04 (no grid table, hence no repeated table
					// header): on paper, a row is never split across two pages.
					await expect(page.locator('.tab-l').first()).toHaveCSS('break-inside', 'avoid');
				}
			}
			await probe.assert();
			expectNoUnexpectedOffLocalRequest(page);
		});
	}
}

test('grid Commune: measured zero, unknown metadata and actual CSV reservations', async ({ page }, testInfo) => {
	await page.setViewportSize({ width: 375, height: 900 });
	await installLocalNetworkGuard(page);
	await mockAuthAnonyme(page);
	const commune = {
		...COMMUNE_FLAT_85047, taux_vacance: 0, annee_vacance: null, nom_epci: '=1+1', loyer_m2_appartement: null,
		etats: {
			...COMMUNE_FLAT_85047.etats,
			annee_vacance: { etat: 'not_reported', motif: 'Millésime non fourni par la source.' },
			loyer_m2_appartement: { etat: 'not_available', motif: 'Loyer d’appartement non publié pour cette commune.' }
		}
	};
	const sources = COMMUNE_META_SOURCE_ORDER.map(dataset => ({
		nom: COMMUNE_META_SOURCE_NOMS[dataset], licence: 'Licence ouverte',
		url: 'javascript:alert(1)', producteur: 'Source synthétique hostile'
	}));
	const probe = await configureSsrSource(page.request, [{
		method: 'GET', pathname: '/v1/commune', query: { code_insee: '85047' }, minCalls: 1, maxCalls: 1,
		responses: [{ status: 200, body: { statut: 'ok', donnees: [commune], meta: { source: sources } } }]
	}]);
	const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
	await page.goto('/commune/85047');
	await hydrated;
	// 04.8-04: the rows are `dl.tab` rows of the theme panels; Immobilier holds vacancy and rents.
	const immobilier = await lireTheme(page, 'immobilier');
	const vacancy = immobilier.locator('.tab-l[data-field-id="vacance.taux_vacance"]');
	await expect(vacancy.locator('.tab-v .state-value')).toHaveText('0 %');
	await expect(vacancy.locator('.tab-v .state-value')).toHaveAttribute('data-state', 'value');
	// The vintage is not served (annee_vacance declared not_reported): both unknown years are
	// qualified explicitly, never invented; the source stays.
	await expect(vacancy.locator('.tab-note-texte')).toHaveText(
		'Vacants : année non renseignée · année du parc privé non renseignée'
	);
	await expect(vacancy.locator('.source-panel-entry', { has: page.locator('dt', { hasText: /^Source$/ }) }).locator('dd')).not.toHaveText('');
	// A hostile source URL never becomes a link, in the themes nor in the source panels.
	await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
	await page.locator('[data-kpi="prix-maisons"] .source-button').click();
	await expect(page.locator('dialog.source-panel[open] a')).toHaveCount(0);
	await page.keyboard.press('Escape');
	await vacancy.getByRole('button', { name: /^Détail : / }).click();
	await expect(page.locator('dialog.source-panel[open]')).toBeVisible();
	await expect(page.locator('dialog.source-panel[open] a')).toHaveCount(0);
	await page.keyboard.press('Escape');
	const rent = immobilier.locator('.tab-l[data-field-id="loyers.loyer_m2_appartement"]');
	await expect(rent.locator('.tab-v .state-absence')).toHaveAttribute('data-state', 'not_available');
	await expect(rent.locator('.tab-v .state-absence .state-badge')).toHaveText('Non publiée');
	await expect(rent.locator('.tab-v .state-value')).toHaveCount(0);
	const downloading = page.waitForEvent('download');
	await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
	await page.getByRole('dialog', { name: 'Exporter les données' }).getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
	const download = await downloading;
	const csv = readFileSync((await download.path())!, 'utf8');
	writeFileSync(testInfo.outputPath('commune-private-housing.csv'), csv);
	expect(csv).toContain('Thème,Indicateur,Valeur ou motif,État,Maille,');
	// C1 (arbitrated by Ianis on 2026-11-10): the CSV follows the categories of the page; the vacancy is read in
	// « Logement et DPE », the rents in « Prix et ventes » (both were exported under « Immobilier »).
	expect(csv).toContain('Logement et DPE,Taux de vacance du parc privé,0 %,value,Parc privé communal,');
	expect(csv).toContain('parc privé');
	expect(csv).toContain("'=1+1");
	expect(csv).toContain('Prix et ventes,Loyer d\'annonce prédit appartement,Non publiée,not_available,');
	// The same substantive checks reject corruptions; no approved golden is rewritten.
	expect(() => expect(csv.replaceAll('parc privé', 'ensemble des logements')).toContain('parc privé')).toThrow();
	expect(() => expect(csv.replace('Taux de vacance du parc privé,0 %', 'Taux de vacance du parc privé,9 %')).toContain('Logement et DPE,Taux de vacance du parc privé,0 %')).toThrow();
	expect(() => expect(csv.replace("'=1+1", '=1+1')).toContain("'=1+1")).toThrow();
	await probe.assert();
	expectNoUnexpectedOffLocalRequest(page);
});

for (const surface of surfaces) {
	test(`grid ${surface.name}: actual browser zoom 200 percent`, async ({}, testInfo) => {
		// A local MV3 extension invokes Chromium's real tab zoom. CSS zoom and CDP
		// pageScaleFactor do not reproduce browser media-query behavior.
		const extension = mkdtempSync(join(tmpdir(), 'dc59-zoom-extension-')); // Outside Vite's watched workspace.
		mkdirSync(extension, { recursive: true });
		writeFileSync(`${extension}/manifest.json`, JSON.stringify({ manifest_version: 3, name: 'Local grid zoom test', version: '1.0', permissions: ['tabs'], background: { service_worker: 'background.js' } }));
		writeFileSync(`${extension}/background.js`, 'chrome.runtime.onInstalled.addListener(() => {});');
		const context = await chromium.launchPersistentContext('', {
			channel: 'chromium', headless: true, baseURL: E2E_APP_ORIGIN, viewport: { width: 1280, height: 900 },
			args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
		});
		try {
			const worker = context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker');
			const page = await context.newPage();
			let probe: Awaited<ReturnType<typeof configureSsrSource>>;
			if (surface.name === 'commune') {
				await installLocalNetworkGuard(page);
				await mockAuthAnonyme(page);
				const sources = COMMUNE_META_SOURCE_ORDER.map(dataset => ({
					nom: COMMUNE_META_SOURCE_NOMS[dataset], licence: 'Licence ouverte',
					url: 'https://www.data.gouv.fr/', producteur: 'Producteur public'
				}));
				probe = await configureSsrSource(page.request, [{
					method: 'GET', pathname: '/v1/commune', query: { code_insee: CODE_INSEE }, minCalls: 1, maxCalls: 1,
					responses: [{ status: 200, body: { statut: 'ok', donnees: [{ ...COMMUNE_FLAT_85047, ...COMMUNE_INDICATORS_SYNTHETIC }], meta: { source: sources } } }]
				}]);
			} else {
				probe = await configure(page, surface);
			}
			const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
			await page.goto(surface.path);
			await hydrated;
			const before = await snapshot(page, surface);
			const zoom = await worker.evaluate(async (appOrigin) => {
				const api = (globalThis as any).chrome;
				const tabs = await api.tabs.query({ url: `${appOrigin}/*` });
				if (tabs.length !== 1) throw new Error('Expected one local test tab');
				await api.tabs.setZoom(tabs[0].id, 2);
				return api.tabs.getZoom(tabs[0].id);
			}, E2E_APP_ORIGIN);
			expect(zoom).toBe(2);
			for (const width of [320, 375, 1280]) {
				await page.setViewportSize({ width, height: 900 });
				await expect.poll(() => page.evaluate(expected => Math.abs(innerWidth - expected), width / 2)).toBeLessThanOrEqual(0.5);
				const layout = await page.evaluate(() => ({ viewport: innerWidth, scroll: document.documentElement.scrollWidth, overflowing: [...document.querySelectorAll('*')].map(el => ({ tag: el.tagName, cls: el.className, text: el.textContent?.slice(0,80), rect: el.getBoundingClientRect().toJSON() })).filter(el => el.rect.right > innerWidth + 1 || el.rect.left < -1) }));
				writeFileSync(testInfo.outputPath(`zoom-${width}.json`), JSON.stringify({ zoom, ...layout }, null, 2));
				expect(layout.scroll).toBeLessThanOrEqual(layout.viewport);
				expect(await snapshot(page, surface)).toEqual(before);
				if (surface.name === 'commune') {
					// 04.8-08 (D-31): the first DPE of the page is spelled out.
					for (const { label } of before as { label: string }[]) {
						await expect(page.locator('.kpi-strip dt.kpi-label').getByText(label, { exact: true })).toBeVisible();
					}
				} else {
					await expect(page.getByRole('heading', { level: 3, name: /documents DPE déclarés à cette adresse/i })).toBeVisible();
				}
				await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
				await page.screenshot({ path: testInfo.outputPath(`zoom-${width}-viewport.png`) });
			}
			await probe.assert();
			expectNoUnexpectedOffLocalRequest(page);
		} finally { await context.close(); }
	});
}

test('grid Commune: open source panel remains within intermediate viewport', async ({ page }, testInfo) => {
	await page.setViewportSize({ width: 521, height: 900 });
	const probe = await configure(page, surfaces[0]);
	const hydrated = page.waitForResponse(response => response.url().endsWith('/auth/me'));
	await page.goto(surfaces[0].path);
	await hydrated;
	await page.locator('[data-kpi="prix-maisons"] .source-button').click();
	const panel = page.locator('dialog.source-panel[open]');
	await expect(panel).toBeVisible();
	// Measured at rest: the 150ms opening motion slides the panel in from the right.
	await panel.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished)));
	const box = await panel.boundingBox();
	writeFileSync(testInfo.outputPath('open-info.json'), JSON.stringify(box));
	await page.screenshot({ path: testInfo.outputPath('open-info.png') });
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.x + box!.width).toBeLessThanOrEqual(521 + 0.5);
	expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(521);
	await probe.assert();
	expectNoUnexpectedOffLocalRequest(page);
});
