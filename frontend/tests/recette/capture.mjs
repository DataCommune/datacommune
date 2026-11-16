// Read-only screen captures of the recette server (plan 03-18), headless, isolated context
// (AGENTS.md: never a personal browser, never a GUI, never CDP on an existing session).
//
// Usage: node frontend/tests/recette/capture.mjs <baseUrl> <outDir> [--themes] [--volet] [--differences] [--adresses <fichier>]
//   <baseUrl>      the recette server (scripts/serve-recette.ps1), e.g. http://<LAN address of the workstation>:5173
//   <outDir>       a directory OUTSIDE the repository (refused otherwise): PNG, rendered text, index.json
//   --themes       fiches and address pages: also a capture with the themes opened
//   --volet        fiches: also a capture with the source panel ⓘ of marker ① opened, then Escape
//   --differences  comparator « 4-mixte »: also a capture with « Différences seulement » checked
//   --adresses <fichier>  phase 4 (plan 04-09): also the address screens of the private keys file
//                  (frontend/tests/recette/private/adresses-reel.json, ignored by git): the rich address,
//                  the address without DPE, its most recent document (first card), the address and DPE
//                  comparators. Screens are named by role, never by key; index.json (outside the
//                  repository) keeps the URLs.
// The comparator « 4-mixte » also gets a window capture after the page scrolled past the real column
// header (the duplicated header band in view).
//
// Read-only: pages are opened, never submitted; no key is read or written here (the server holds the
// session key). Throttled: one page at a time and a pause between navigations, far below the
// ceiling of 30 requests per minute of the session key. Outside the Playwright gate: the recette
// config only collects `*.recette.ts`.
import { chromium } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, isAbsolute, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const iAdresses = args.indexOf('--adresses');
const fichierAdresses = iAdresses >= 0 ? args[iAdresses + 1] : null;
const restants = iAdresses >= 0 ? args.filter((_, i) => i !== iAdresses && i !== iAdresses + 1) : args;
const options = new Set(restants.filter((arg) => arg.startsWith('--')));
const [base, outDir] = restants.filter((arg) => !arg.startsWith('--'));
const connues = new Set(['--themes', '--volet', '--differences']);
if (!base || !outDir || [...options].some((option) => !connues.has(option)) || (iAdresses >= 0 && !fichierAdresses)) {
	console.error('usage: node frontend/tests/recette/capture.mjs <baseUrl> <outDir> [--themes] [--volet] [--differences] [--adresses <fichier>]');
	process.exit(2);
}
const depot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const sortie = resolve(outDir);
const relatif = relative(depot, sortie);
// `..captures` is a folder of the repository: only `..` itself or `..` + separator leaves it (D-15 IN-09).
const dehors = relatif === '..' || relatif.startsWith(`..${sep}`) || isAbsolute(relatif);
if (!dehors) {
	console.error(`refused: ${sortie} is inside the repository; captures are never committed`);
	process.exit(2);
}
mkdirSync(sortie, { recursive: true });

const PAUSE_MS = 15_000; // a fiche fires several API calls: about 4 pages a minute
const WIDTHS = [
	{ name: 'd1280', width: 1280, height: 900, mobile: false },
	{ name: 'm375', width: 375, height: 812, mobile: true },
	{ name: 't768', width: 768, height: 1024, mobile: false, only: ['commune-44109', 'comparer-4-mixte'] }
];
// Commune screens only; real states of the work base (44/85 hosted, 35238 out of coverage, 99999 unknown).
const SCREENS = [
	['accueil', '/'],
	['communes-q-nantes', '/communes?q=Nantes'],
	['communes-cp-44000', '/communes?q=44000'],
	['communes-cp-33000-hors', '/communes?q=33000'],
	['communes-dep-85', '/communes?departement=85'],
	['communes-vide', '/communes?q=zzzzqqq'],
	['commune-44109', '/commune/44109'],
	['commune-85191', '/commune/85191'],
	['commune-85262', '/commune/85262'],
	['commune-85001', '/commune/85001'],
	['commune-35238-hors', '/commune/35238'],
	['commune-99999-inconnue', '/commune/99999'],
	['comparer-vide', '/comparer'],
	['comparer-2', '/comparer?communes=44109,85191'],
	['comparer-4-mixte', '/comparer?communes=44109,85191,44020,35238'],
	['comparer-lien-altere', '/comparer?communes=44109,ABCDE']
];

// Phase 4 screens of the private keys (--adresses). The document and the DPE comparator are resolved
// on the rich address page (first cards), at each width; no key is printed.
const ECRANS_ADRESSES = [];
if (fichierAdresses) {
	if (!existsSync(fichierAdresses)) {
		console.error(`refused: private keys file not found: ${fichierAdresses}`);
		process.exit(2);
	}
	const cles = JSON.parse(readFileSync(fichierAdresses, 'utf8'));
	for (const champ of ['adresse_riche', 'adresse_b', 'adresse_sans_dpe']) {
		if (typeof cles[champ] !== 'string' || !cles[champ]) {
			console.error(`refused: field ${champ} missing from the private keys file`);
			process.exit(2);
		}
	}
	ECRANS_ADRESSES.push(
		['adresse-riche', `/adresse/${encodeURIComponent(cles.adresse_riche)}`],
		['adresse-sans-dpe', `/adresse/${encodeURIComponent(cles.adresse_sans_dpe)}`],
		['dpe-plus-recent', null],
		['comparer-adresses', `/comparer?adresses=${encodeURIComponent(cles.adresse_riche)},${encodeURIComponent(cles.adresse_b)}`],
		['comparer-dpe', null]
	);
}

const sleep = (ms) => new Promise((fin) => setTimeout(fin, ms));
const browser = await chromium.launch({ headless: true });
const index = [];

async function capturer(page, fichier, fullPage = true) {
	await page.screenshot({ path: join(sortie, `${fichier}.png`), fullPage });
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
	return { fichier: `${fichier}.png`, horizontalOverflow: overflow };
}

try {
	for (const w of WIDTHS) {
		const context = await browser.newContext({
			viewport: { width: w.width, height: w.height },
			isMobile: w.mobile,
			hasTouch: w.mobile,
			locale: 'fr-FR',
			deviceScaleFactor: 1
		});
		const page = await context.newPage();
		const consoleErrors = [];
		page.on('console', (message) => {
			if (message.type() === 'error') consoleErrors.push(message.text());
		});
		// Numbers of the first two documents of the rich address, read on its page at this width.
		let numeros = [];
		for (const [name, chemin] of [...SCREENS, ...ECRANS_ADRESSES]) {
			// The address screens are captured at every width; `only` restricts the commune screens.
			if (w.only && !w.only.includes(name) && !ECRANS_ADRESSES.some(([nom]) => nom === name)) continue;
			let path = chemin;
			if (name === 'dpe-plus-recent') path = numeros[0] ? `/dpe/${numeros[0]}` : null;
			if (name === 'comparer-dpe') path = numeros.length >= 2 ? `/comparer?dpe=${numeros[0]},${numeros[1]}` : null;
			if (path === null) {
				index.push({ name, width: w.name, captures: [], error: 'documents of the rich address not read' });
				continue;
			}
			const url = new URL(path, base).toString();
			const debut = Date.now();
			const entree = { name, width: w.name, url, captures: [] };
			try {
				const reponse = await page.goto(url, { waitUntil: 'networkidle', timeout: 45_000 });
				entree.status = reponse?.status() ?? null;
				await sleep(800);
				entree.title = await page.title();
				entree.captures.push(await capturer(page, `${name}--${w.name}`));
				writeFileSync(join(sortie, `${name}--${w.name}.txt`), await page.evaluate(() => document.body.innerText), 'utf8');

				if (name === 'adresse-riche') {
					numeros = await page.locator('ol.doclist-cards a[href^="/dpe/"]').evaluateAll((liens) =>
						liens.map((lien) => lien.getAttribute('href').slice('/dpe/'.length))
					);
				}
				const fiche = (name.startsWith('commune-') || name.startsWith('adresse-')) && (await page.locator('[data-kpi]').count()) > 0;
				if (fiche && options.has('--themes')) {
					await page.evaluate(() => document.querySelectorAll('main details').forEach((details) => (details.open = true)));
					await sleep(300);
					entree.captures.push(await capturer(page, `${name}--${w.name}--themes`));
				}
				if (fiche && options.has('--volet')) {
					await page.evaluate(() => window.scrollTo(0, 0));
					await page.locator('[data-kpi="prix-maisons"] .source-button').click();
					await page.getByRole('dialog').waitFor({ state: 'visible' });
					await sleep(300);
					entree.captures.push(await capturer(page, `${name}--${w.name}--volet`, false));
					await page.keyboard.press('Escape');
				}
				if (name === 'comparer-4-mixte') {
					// The page scrolls past the real column header: the duplicated band shows under the site header.
					await page.evaluate(() => {
						const thead = document.querySelector('thead');
						if (thead) window.scrollTo(0, window.scrollY + thead.getBoundingClientRect().bottom + 120);
					});
					await sleep(500);
					entree.bande = await page.evaluate(() => {
						const bande = document.querySelector('.compare-bande');
						return bande ? getComputedStyle(bande).visibility : 'absente';
					});
					entree.captures.push(await capturer(page, `${name}--${w.name}--defile`, false));
					await page.evaluate(() => window.scrollTo(0, 0));
					if (options.has('--differences')) {
						await sleep(PAUSE_MS);
						await page.getByRole('checkbox', { name: 'Différences seulement' }).check();
						await page.waitForLoadState('networkidle');
						await sleep(800);
						entree.captures.push(await capturer(page, `${name}--${w.name}--differences`));
					}
				}
			} catch (erreur) {
				entree.error = String(erreur?.message ?? erreur);
			}
			entree.ms = Date.now() - debut;
			entree.horizontalOverflow = entree.captures.some((capture) => capture.horizontalOverflow);
			entree.consoleErrors = consoleErrors.splice(0);
			index.push(entree);
			console.log(`${w.name} ${name} ${entree.status ?? 'ERR'} captures=${entree.captures.length}${entree.error ? ` ${entree.error}` : ''}`);
			await sleep(PAUSE_MS);
		}
		await context.close();
	}
} finally {
	await browser.close();
	writeFileSync(join(sortie, 'index.json'), JSON.stringify(index, null, 2), 'utf8');
}
