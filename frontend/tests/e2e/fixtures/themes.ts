import { expect, type Locator, type Page } from '@playwright/test';

/**
 * The categories of the commune fiche (4.9) that hold the rows of each former theme: the rows of a theme
 * are placed by their served field (`commune-categories.ts`), several themes over several categories.
 */
const CATEGORIES_DU_THEME: Readonly<Record<string, readonly string[]>> = {
	immobilier: ['q-prix', 'q-logement'],
	'energie-bati': ['q-logement'],
	'services-transports': ['q-quotidien', 'q-logement'],
	'contexte-communal': ['q-quotidien', 'q-prix', 'q-logement'],
	'risques-urbanisme': ['q-prix', 'q-logement']
};

/**
 * Reads a theme of the commune fiche. Since 4.9 its rows sit in the categories of the address fiche: open
 * cards above 1080 px, closed folds under it, which the reader opens as a reader would; nothing is read
 * from the API. `id` is the former anchor of the theme. Returns the block of the categories, where each
 * row is once: a test finds a row of the theme by its field.
 */
export async function lireTheme(page: Page, id: string): Promise<Locator> {
	const categories = CATEGORIES_DU_THEME[id];
	if (!categories) throw new Error(`Unknown commune theme ${id}`);
	for (const categorie of categories) {
		const pli = page.locator(`details.cat#${categorie}`);
		if (!(await pli.count())) continue;
		if (!(await pli.evaluate((node: HTMLDetailsElement) => node.open))) await pli.locator(':scope > summary').click();
		await expect(pli).toHaveJSProperty('open', true);
	}
	const lecture = page.locator('section.lecture');
	const premiere = page.locator(`details.cat#${categories[0]}`);
	await premiere.scrollIntoViewIfNeeded();
	await expect(premiere.locator('dl.tab > .tab-l').first()).toBeVisible();
	return lecture;
}
