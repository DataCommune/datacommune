import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';

// Review 2026-11-10, finding R-01 (red on purpose, no fix here).
//
// `ExportDialog.svelte` declares `role="dialog" aria-modal="true"` on a plain `div`: nothing moves the
// focus into it, nothing keeps it there, nothing gives it back, and the page behind stays reachable.
// The source panel of the same fiche (`SourcePanel.svelte`) is a native `<dialog>` and does all four.
// A keyboard or screen-reader visitor who opens « Télécharger (CSV) » is told a modal dialog is open
// while their focus is still on the page behind it.

const MOCK_COMMUNE_85047 = {
	statut: 'ok',
	donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC }],
	meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
};

async function ouvrirFiche(page: Page) {
	await configureSsrSource(page.request, [
		{ method: 'GET', pathname: '/v1/commune', query: { code_insee: '85047' }, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: MOCK_COMMUNE_85047 }] }
	]);
	const hydrated = page.waitForResponse((response) => response.url().endsWith('/auth/me'));
	await page.goto('/commune/85047');
	await hydrated;
}

async function ouvrirExportAuClavier(page: Page) {
	const declencheur = page.getByRole('button', { name: 'Télécharger (CSV)' });
	await declencheur.focus();
	await page.keyboard.press('Enter');
	const dialogue = page.getByRole('dialog', { name: 'Exporter les données' });
	await expect(dialogue).toBeVisible();
	return { declencheur, dialogue };
}

const focusDansDialogue = (page: Page) =>
	page.evaluate(() => document.querySelector('.export-dialog')?.contains(document.activeElement) ?? false);

test.describe('R-01 — dialogue d’export : focus, piège, retour, page inerte', () => {
	test.use({ contextOptions: { reducedMotion: 'reduce' } });

	test('à l’ouverture au clavier, le focus entre dans le dialogue', async ({ page }) => {
		await ouvrirFiche(page);
		await ouvrirExportAuClavier(page);
		expect(await focusDansDialogue(page), 'focus still on the page behind the modal').toBe(true);
	});

	test('Tab ne sort jamais du dialogue ouvert', async ({ page }) => {
		await ouvrirFiche(page);
		const { dialogue } = await ouvrirExportAuClavier(page);
		// Start from inside, as a visitor who clicked a control of the dialog would.
		await dialogue.getByRole('button', { name: 'Annuler' }).focus();
		const sorties: string[] = [];
		for (let i = 0; i < 6; i += 1) {
			await page.keyboard.press('Tab');
			if (!(await focusDansDialogue(page))) {
				sorties.push(await page.evaluate(() => (document.activeElement?.textContent ?? document.activeElement?.tagName ?? '').trim().slice(0, 60)));
			}
		}
		expect(sorties, 'focus stops reached outside the open modal').toEqual([]);
	});

	test('Échap rend le focus au bouton « Télécharger (CSV) »', async ({ page }) => {
		await ouvrirFiche(page);
		const { declencheur, dialogue } = await ouvrirExportAuClavier(page);
		await dialogue.getByRole('button', { name: 'Annuler' }).focus();
		await page.keyboard.press('Escape');
		await expect(dialogue).toBeHidden();
		await expect(declencheur).toBeFocused();
	});

	test('dialogue ouvert : la page derrière est inerte', async ({ page }) => {
		await ouvrirFiche(page);
		await ouvrirExportAuClavier(page);
		// A native modal `<dialog>` makes the rest of the document inert; here the links of the header
		// stay in the tab order and in the accessibility tree.
		// Probe rewritten with the fix (2026-11-10): a native modal sets no attribute on the page behind,
		// so the former probe (`closest('[inert]')`) would have failed the healthy source panel too. The
		// inertness itself is measured: an inert link cannot take the focus. Seen red on the former `div`.
		const atteignable = await page.evaluate(() => {
			const lien = document.querySelector<HTMLElement>('header a[href]');
			if (!lien) return 'no probe';
			if (lien.closest('[inert]') || lien.closest('[aria-hidden="true"]')) return 'inert';
			lien.focus();
			return document.activeElement === lien ? 'reachable' : 'inert';
		});
		expect(atteignable).toBe('inert');
	});

	test('témoin : axe ne voit rien, dialogue ouvert (le défaut n’est pas détectable par axe)', async ({ page }) => {
		await ouvrirFiche(page);
		await ouvrirExportAuClavier(page);
		const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
		expect(axe.violations.map((v) => v.id)).toEqual([]);
	});
});
