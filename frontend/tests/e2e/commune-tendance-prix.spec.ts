import { test, expect, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import type { TendancePrix } from '../../src/lib/api/commune';
import { TENDANCE_EVOLUTION, TENDANCE_INSUFFISANTE, TENDANCE_POINTS } from '../../src/lib/data/__fixtures__/commune-tendance';
import { configureSsrSource, type SsrSourceProbe } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW } from './fixtures/commune-indicators';

// Price trend of the commune fiche over the closed DVF years (decision of Ianis of 2026-11-11,
// migration 115, field `tendance_prix` of /v1/commune). On fixtures: the three states of the tile at
// 1440 and 375 px (a served change with its curve and its computed period; points without a
// percentage; no tile under two medians, the rows say it), the yearly medians and counts behind the information button,
// and the theme rows that replace « Non publiée ». The figures are synthetic.
//
// The first load is SSR: every case configures the local HTTP source with the exact read, then checks
// its fail-closed journal.

const MOCK_USER = { id: 2, email: 'test@datacommune.fr', name: 'Test User', is_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' };
const LARGEURS = [1440, 375] as const;
const ETATS: { nom: string; tendance: TendancePrix }[] = [
	{ nom: 'evolution', tendance: TENDANCE_EVOLUTION },
	{ nom: 'points', tendance: TENDANCE_POINTS },
	{ nom: 'insuffisant', tendance: TENDANCE_INSUFFISANTE }
];

const net = (texte: string | null) => (texte ?? '').replace(/[  ]/g, ' ').replace(/\s+/g, ' ').trim();

function reponse(tendance: TendancePrix | null) {
	return {
		statut: 'ok',
		donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, indicateurs_dvf: null, indicateurs_dpe: null, tendance_prix: tendance }],
		meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
	};
}

async function ouvrir(page: Page, tendance: TendancePrix | null, largeur: number): Promise<SsrSourceProbe> {
	await page.setViewportSize({ width: largeur, height: 900 });
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
	const source = await configureSsrSource(page.request, [
		{ method: 'GET', pathname: '/v1/commune', query: { code_insee: '85047' }, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: reponse(tendance) }] }
	]);
	const hydrate = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
	const response = await page.goto('/commune/85047');
	await hydrate;
	expect(response?.status()).toBe(200);
	return source;
}

async function capture(page: Page, testInfo: { outputPath(name: string): string }, nom: string) {
	const dossier = process.env.DC_TENDANCE_EVIDENCE_DIR?.replace(/[\\/]$/, '');
	if (dossier) await mkdir(dossier, { recursive: true });
	await page.locator('#kpi-tendance').screenshot({ path: dossier ? `${dossier}/${nom}` : testInfo.outputPath(nom) });
}

async function sansDebordement(page: Page) {
	const layout = await page.evaluate(() => ({ viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
	expect(layout.scrollWidth, `document ${layout.scrollWidth}px > viewport ${layout.viewport}px`).toBeLessThanOrEqual(layout.viewport);
	const tuile = await page.locator('#kpi-tendance').evaluate((noeud) => ({ scroll: noeud.scrollWidth, client: noeud.clientWidth }));
	expect(tuile.scroll, 'la tuile ne déborde pas').toBeLessThanOrEqual(tuile.client);
}

test.describe('Fiche commune — tendance du prix sur les années closes', () => {
	for (const largeur of LARGEURS) {
		test(`évolution chiffrée à ${largeur} px : pourcentage, cinq points reliés, période servie, type le plus vendu d’abord`, async ({ page }, testInfo) => {
			const source = await ouvrir(page, TENDANCE_EVOLUTION, largeur);
			const tuile = page.locator('#kpi-tendance');
			await expect(tuile.locator('.kpi-label')).toHaveText('Tendance du prix médian');
			const lignes = tuile.locator('.kpi-line');
			await expect(lignes.locator('.kpi-sub')).toHaveText(['Appartements', 'Maisons']);
			const appartements = lignes.nth(0);
			expect(net(await appartements.locator('.state-value').textContent())).toBe('+12 %');
			await expect(appartements.locator('[data-periode]')).toHaveText('2021 → 2025');
			await expect(appartements.locator('svg[data-courbe] circle')).toHaveCount(5);
			await expect(appartements.locator('svg[data-courbe] line')).toHaveCount(4);
			// Houses of the fixture: points, no percentage on their line.
			await expect(lignes.nth(1).locator('.state-badge')).toHaveText('Évolution non chiffrée');
			expect(net(await lignes.nth(1).textContent())).not.toMatch(/\d ?%/);
			await expect(page.getByText('Ce site ne publie pas l’évolution des prix pour cette commune.')).toHaveCount(0);
			await sansDebordement(page);
			await capture(page, testInfo, `tendance-evolution-${largeur}.png`);
			await source.assert();
		});

		test(`points sans pourcentage à ${largeur} px : la courbe garde un trou, aucun chiffre d’évolution`, async ({ page }, testInfo) => {
			const source = await ouvrir(page, TENDANCE_POINTS, largeur);
			const tuile = page.locator('#kpi-tendance');
			await expect(tuile.locator('.kpi-line')).toHaveCount(1);
			await expect(tuile.locator('.kpi-sub')).toHaveText('Maisons');
			await expect(tuile.locator('.kpi-line .state-badge')).toHaveText('Évolution non chiffrée');
			await expect(tuile.locator('.state-value')).toHaveCount(0);
			await expect(tuile.locator('svg[data-courbe] circle')).toHaveCount(4);
			await expect(tuile.locator('svg[data-courbe] line')).toHaveCount(2);
			await expect(tuile.locator('[data-periode]')).toHaveText('2021 → 2025');
			await sansDebordement(page);
			await capture(page, testInfo, `tendance-points-${largeur}.png`);
			await source.assert();
		});

		test(`effectif insuffisant à ${largeur} px : aucune tuile, l’état est dit par les lignes de « Prix au m² »`, async ({ page }, testInfo) => {
			const source = await ouvrir(page, TENDANCE_INSUFFISANTE, largeur);
			await expect(page.locator('#kpi-tendance')).toHaveCount(0);
			await expect(page.locator('svg[data-courbe]')).toHaveCount(0);
			// Under 1081 px a category is a closed fold: open the one of the prices, as a reader does.
			const categorie = page.locator('details.cat#q-prix');
			if ((await categorie.count()) && !(await categorie.evaluate((noeud: HTMLDetailsElement) => noeud.open))) await categorie.locator(':scope > summary').click();
			const lignes = page.locator('[data-ligne^="dvf-tendance-"]');
			await expect(lignes).toHaveCount(2);
			for (const index of [0, 1]) expect(net(await lignes.nth(index).textContent())).toContain('Effectif insuffisant');
			await lignes.first().scrollIntoViewIfNeeded();
			const layout = await page.evaluate(() => ({ viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
			expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport);
			const dossier = process.env.DC_TENDANCE_EVIDENCE_DIR?.replace(/[\\/]$/, '');
			const nom = `tendance-insuffisant-${largeur}.png`;
			await lignes.first().screenshot({ path: dossier ? `${dossier}/${nom}` : testInfo.outputPath(nom) });
			await source.assert();
		});
	}

	test('le bouton d’information ouvre le détail : médianes et effectifs de chaque année, méthode, limite', async ({ page }) => {
		const source = await ouvrir(page, TENDANCE_EVOLUTION, 1440);
		await page.locator('#kpi-tendance').getByRole('button', { name: 'Détail : Tendance du prix médian' }).click();
		const volet = page.locator('#kpi-tendance dialog[open]');
		await expect(volet).toBeVisible();
		const texte = net(await volet.textContent());
		expect(texte).toContain('Appartements, année par année');
		expect(texte).toContain('2021 : 3 500 €/m², 3 996 ventes');
		expect(texte).toContain('2025 : 3 920 €/m², 3 200 ventes');
		expect(texte).toContain('2023 : 4 ventes, prix médian non publié');
		expect(texte).toContain('2021 → 2025, années civiles entières');
		expect(texte).toContain('Il n’est chiffré que si chacune de ces deux années compte au moins 30 ventes');
		expect(texte).toContain('Le prix médian suit ce qui s’est vendu');
		// Never the change of a dwelling, never a forecast.
		expect(texte).not.toMatch(/votre bien|a pris|prévoit|devrait/i);
		await source.assert();
	});

	test('les lignes du groupe « Prix au m² » remplacent « Non publiée », une par type', async ({ page }) => {
		const source = await ouvrir(page, TENDANCE_EVOLUTION, 1440);
		const lignes = page.locator('[data-ligne^="dvf-tendance-"]');
		await expect(lignes).toHaveCount(2);
		expect(await lignes.evaluateAll((noeuds) => noeuds.map((noeud) => noeud.getAttribute('data-ligne')))).toEqual(['dvf-tendance-appartement', 'dvf-tendance-maison']);
		expect(net(await lignes.nth(0).textContent())).toContain('Tendance du prix médian des appartements');
		expect(net(await lignes.nth(0).textContent())).toContain('+12 %');
		expect(net(await lignes.nth(1).textContent())).toContain('Évolution non chiffrée');
		await expect(page.locator('[data-ligne="dvf-tendance"]')).toHaveCount(0);
		await source.assert();
	});

	test('sans série servie, la fiche garde sa ligne « Non publiée » et aucune tuile de tendance', async ({ page }) => {
		const source = await ouvrir(page, null, 1440);
		await expect(page.locator('#kpi-tendance')).toHaveCount(0);
		await expect(page.locator('[data-ligne="dvf-tendance"]')).toHaveCount(1);
		await expect(page.locator('[data-ligne^="dvf-tendance-"]')).toHaveCount(0);
		await source.assert();
	});

	for (const { nom, tendance } of ETATS) {
		test(`aucune erreur de console sur la fiche, état « ${nom} »`, async ({ page }) => {
			const erreurs: string[] = [];
			page.on('console', (message) => {
				if (message.type() === 'error') erreurs.push(message.text());
			});
			page.on('pageerror', (erreur) => erreurs.push(String(erreur)));
			const source = await ouvrir(page, tendance, 1440);
			await expect(page.locator('#kpi-tendance')).toHaveCount(nom === 'insuffisant' ? 0 : 1);
			expect(erreurs).toEqual([]);
			await source.assert();
		});
	}
});
