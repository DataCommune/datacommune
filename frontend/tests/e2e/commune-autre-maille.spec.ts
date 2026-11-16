// Paris, Lyon, Marseille: a value published at the other mesh, on the rendered fiche (R-09). Headless runner,
// isolated context, every SSR read served by the local fail-closed source (AGENTS.md).
//
// The served rows are production exports of 2026-11-10 passed through the API projection of this branch
// (`src/lib/data/__fixtures__/commune-*-autre-maille.json`):
//   - the 11e shows the ABC zone and the taxes of Paris, the head of the page saying which values hold for the
//     whole commune, and its fibre row leads to the fiche of Paris without taking its figure;
//   - Paris shows no rent, says it is published by arrondissement and lists its twenty arrondissements.
import { readFileSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';

const LECTURES = 12;
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const MOCK_USER = { id: 2, email: 'test@datacommune.fr', name: 'Test User', is_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' };
const NBSP = String.fromCharCode(0xa0);

const exportServi = (code: string): { donnees: unknown; sources: unknown } =>
	JSON.parse(readFileSync(new URL(`../../src/lib/data/__fixtures__/commune-${code}-autre-maille.json`, import.meta.url), 'utf-8'));
const EXPORTS: Record<string, { donnees: unknown; sources: unknown }> = { '75111': exportServi('75111'), '75056': exportServi('75056') };

function lecture(code: string): SourceExpectation {
	const body = { statut: 'ok', donnees: [EXPORTS[code].donnees], meta: { source: EXPORTS[code].sources } };
	return {
		method: 'GET',
		pathname: '/v1/commune',
		query: { code_insee: code },
		minCalls: 0,
		maxCalls: LECTURES,
		responses: Array.from({ length: LECTURES }, () => ({ status: 200, body }))
	};
}

async function ouvrir(page: Page, code: string) {
	await configureSsrSource(page.request, [lecture(code)]);
	await page.setViewportSize({ width: 1440, height: 900 });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(`/commune/${code}`);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
});

test.describe('R-09 — la valeur publiée à l’autre maille', () => {
	test('le 11e montre la zone et les taux de Paris, marqués « Commune », et renvoie vers Paris pour la fibre', async ({ page }) => {
		await ouvrir(page, '75111');
		await expect(page.locator('.essentiel-chapo')).toContainText('sauf celles marquées');
		await expect(page.locator('.essentiel-chapo')).toContainText('qui valent pour tout Paris');

		const zone = page.locator('main [data-ligne="zonage_abc.zone"]');
		await expect(zone).toContainText('Abis');
		await expect(zone.locator('.ech')).toContainText('Commune');
		await expect(page.locator('main [data-ligne="fiscalite.taux_tfpb"] .ech')).toContainText('Commune');
		// Its own rent carries no mark of the commune.
		await expect(page.locator('main [data-ligne="loyers.loyer_m2_appartement"] .ech')).toHaveCount(0);

		const fibre = page.locator('main [data-ligne="connectivite.fibre_pct"]');
		await expect(fibre).toContainText(`Publié pour la commune entière${NBSP}: voir la fiche de Paris.`);
		await expect(fibre.getByRole('link', { name: 'Fiche de Paris' })).toHaveAttribute('href', '/commune/75056');
		await expect(fibre).not.toContainText('95,4');

		// The open JSON export carries the origin and the mesh as the API served them.
		const exporte = await (await page.request.get('/commune/75111.json')).json();
		expect(exporte.donnees.heritages.zone_abc).toEqual({ echelle: 'commune', code_insee: '75056', nom: 'Paris' });
		expect(exporte.donnees.etats.fibre_pct.publiee_a).toBe('commune');

		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => v.id)).toEqual([]);
	});

	test('Paris ne montre aucun loyer, dit qu’il est publié par arrondissement et liste ses vingt arrondissements', async ({ page }) => {
		await ouvrir(page, '75056');
		await expect(page.locator('.essentiel-chapo')).toContainText('valent pour la commune entière');

		const loyer = page.locator('main [data-ligne="loyers.loyer_m2_appartement"]');
		await expect(loyer).toContainText('Non renseigné');
		await expect(loyer).toContainText(`Publié par arrondissement${NBSP}: voir les arrondissements de Paris.`);
		await expect(loyer.getByRole('link', { name: 'Arrondissements de Paris' })).toHaveAttribute('href', '#arrondissements');

		const liste = page.locator('#arrondissements');
		await expect(liste).toContainText('Les 20 arrondissements de Paris');
		const liens = liste.getByRole('link');
		await expect(liens).toHaveCount(20);
		await expect(liens.first()).toHaveAttribute('href', '/commune/75101');
		await expect(page.locator('main .ech').filter({ hasText: 'Commune' })).toHaveCount(1); // the head only

		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => v.id)).toEqual([]);
	});
});
