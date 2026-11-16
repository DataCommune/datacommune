// The commune fiche renders a source panel at its first opening, never in the server HTML (Search Console
// of 2026-11-16: the shared texts of the panels made every fiche look like another). A test that reads a
// panel without going through its button mounts it first: opened, then closed, its content stays rendered.
import { expect, type Locator } from '@playwright/test';

/** Opens and closes the first panel under `porteur` (a tile, a row): its entries are then in the DOM. */
export async function monterVolet(porteur: Locator): Promise<void> {
	await porteur.locator('.source-button').first().click();
	await expect(porteur.locator('dialog.source-panel[open]')).toHaveCount(1);
	await porteur.page().keyboard.press('Escape');
	await expect(porteur.locator('dialog.source-panel[open]')).toHaveCount(0);
}
