#!/usr/bin/env node
// Renders scripts/og-image.html to static/og-image.png (1200 x 630), the link preview of every public page.
// Run by hand when the charter changes: `node scripts/og-image.mjs` from frontend/.
import { chromium } from 'playwright';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const ici = dirname(fileURLToPath(import.meta.url));
const navigateur = await chromium.launch();
try {
	const page = await navigateur.newPage({ viewport: { width: 1200, height: 630 } });
	await page.goto(pathToFileURL(join(ici, 'og-image.html')).href);
	await page.evaluate(() => document.fonts.ready);
	await page.screenshot({ path: join(ici, '..', 'static', 'og-image.png') });
} finally {
	await navigateur.close();
}
