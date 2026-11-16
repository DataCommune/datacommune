// The address fiche on the report format (04.7-03, UI-SPEC v3 §§ 10, 12, 13), on three addresses:
// 2 Rue des Essais (Nantes, point outside every parcel, 11 DPE), 31 Boulevard des Exemples (Nantes,
// Géorisques without answer) and a house in Luçon (Vendée). Their reports are in the format of
// 04.7-01 with the values read on the public site on 2026-10-30, anonymised since (fictitious
// addresses, keys, parcels, DPE numbers, sale dates and prices; `scripts/check-real-data.py`) (`fixtures/rapports-maquette.json`,
// built by vers-format-01.mjs of the internal note 2026-10-30-fiche-adresse); the route itself
// was not read (not deployed then). After the deployment, the real reports are captured and this spec
// replayed against them (`DC_RAPPORTS_FIXTURE`, see 04.7-03-SUMMARY « Contrôle après déploiement »). Headless runner, isolated context, every off-local request
// refused (AGENTS.md).
// Checked at 1440, 1024, 768, 375 and 320 px and at 200 % zoom (720 × 450 CSS px): no horizontal
// overflow, every served criterion and no other, axe without violation, the table of contents static
// below 1080 px (04.8-03: no sticky strip), the panel usable; printing opens every criterion and hides
// the chrome. The selectors follow the fiche « Par question » (04.8-03).
import { readFileSync } from 'node:fs';
import { mkdir, readFile } from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import { PREMIERE_PAGE_DECLAREES } from './fixtures/adresse-ventes';
import { attendreAucuneCarteNonReconnue, repondreCarteStatique } from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import { verifierGraisses, verifierPlancherTypo } from './fixtures/quality';
interface Adresse {
	slug: string;
	banId: string;
	rapport: { donnees: { adresse: { libelle: string; code_postal: string; commune: { nom: string } }; themes: { id: string; rattachement: { cle: string | null } | null }[] } };
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}
// `DC_RAPPORTS_FIXTURE` names another fixture of `fixtures/` (the real reports captured after the
// deployment, `capturer-rapports-reels.mjs`); the real values of the mock-up otherwise.
const FIXTURE = process.env.DC_RAPPORTS_FIXTURE ?? 'rapports-maquette.json';
const RAPPORTS = JSON.parse(readFileSync(new URL(`./fixtures/${FIXTURE}`, import.meta.url), 'utf8')) as Adresse[];
// The h1 of each address of the fixture: number, suffix and street as French writes it (voie-lisible.ts).
const TITRES_H1: Record<string, string> = {
	essais: '2 rue des Essais',
	exemples: '31 boulevard des Exemples',
	lucon: '4 chemin du Puits Fictif'
};
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 1024, 768, 375, 320] as const;
const LECTURES = 12;

const repete = (body: unknown) => Array.from({ length: LECTURES }, () => ({ status: 200, body }));
const attente = (pathname: string, query: Record<string, string>, body: unknown) => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 0,
	maxCalls: LECTURES,
	responses: repete(body)
});

// The map of the head comes from our own proxy under `/carte/` (04.7-17), relayed by Caddy in
// production; here the app origin answers its two pictures with a grey 448 × 448 PNG, or with the 503 of a
// failing IGN (`repondreCarteStatique`, strict pattern: any other map path fails the scenario).

async function configure(request: APIRequestContext, a: Adresse) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				attente(cheminRapport(a.banId), REQUETE_RAPPORT, a.rapport),
				attente(`/v1/adresse/${a.banId}/dpe`, { page: '1' }, a.documents),
				attente(`/v1/adresse/${a.banId}/ventes`, { page: '1' }, a.ventes),
				attente(`/v1/adresse/${a.banId}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, a.ventesAdresse)
			]
		}
	});
	expect(res.ok()).toBe(true);
}

async function ouvrir(page: Page, a: Adresse) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(`/adresse/${a.banId}`);
	await hydrated;
	// D-33 (04.8-03): the h1 is composed of the served number, suffix and readable street; the postal code and the
	// commune moved to the identity line under it, so nothing of the served label is lost.
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(TITRES_H1[a.slug]);
	const { code_postal, commune } = a.rapport.donnees.adresse;
	await expect(page.locator('.adresse-identity')).toContainText(`${code_postal} ${commune.nom}`);
}

function preuve(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_RAPPORT_EVIDENCE_DIR;
	return dossier ? `${dossier.replace(/[\\/]$/, '')}/${nom}` : testInfo.outputPath(nom);
}

async function sansDebordement(page: Page, contexte: string) {
	const { large, vue, fautifs } = await page.evaluate(() => {
		const vue = window.innerWidth;
		// The deepest elements that cross the right edge, to name the culprit in the failure.
		const fautifs = [...document.querySelectorAll('body *')]
			.filter((el) => el.getBoundingClientRect().right > vue + 1 && ![...el.children].some((c) => c.getBoundingClientRect().right > vue + 1))
			.slice(0, 5)
			.map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join('.')} → ${Math.round(el.getBoundingClientRect().right)}`);
		return { large: document.documentElement.scrollWidth, vue, fautifs };
	});
	expect(large, `aucun défilement horizontal (${contexte}) : ${fautifs.join(' ; ')}`).toBeLessThanOrEqual(vue);
}

async function sansViolationAxe(page: Page, contexte: string) {
	const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(axe.violations, `${contexte} : ${JSON.stringify(axe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`).toEqual([]);
}

test.beforeAll(async () => {
	if (process.env.DC_RAPPORT_EVIDENCE_DIR) await mkdir(process.env.DC_RAPPORT_EVIDENCE_DIR, { recursive: true });
});

for (const a of RAPPORTS) {
	test.describe(`Fiche du rapport — ${a.rapport.donnees.adresse.libelle}`, () => {
		test.beforeEach(async ({ page, request }) => {
			await installLocalNetworkGuard(page);
			await repondreCarteStatique(page);
			await configure(request, a);
		});

		test.afterEach(async ({ page }) => {
			attendreAucuneCarteNonReconnue(page);
		});

		test('carte de l’en-tête : images de l’origine du site sous /carte/, deux colonnes à 1440, une en dessous, aucun tiers', async ({ page }, testInfo) => {
			const demandees = await repondreCarteStatique(page);
			await page.setViewportSize({ width: 1440, height: 900 });
			await ouvrir(page, a);
			const carte = page.locator('.adresse-carte figure.carte');
			const plan = carte.locator('img').first();
			await plan.scrollIntoViewIfNeeded();
			await expect.poll(() => plan.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth)).toBe(448);
			expect(await carte.locator('img').evaluateAll((imgs) => imgs.map((i) => i.getAttribute('src')))).toEqual([
				`/carte/${a.banId}/plan.png`,
				`/carte/${a.banId}/parcelles.png`
			]);
			expect(demandees.length).toBeGreaterThan(0);
			for (const url of demandees) expect(new URL(url).origin).toBe(E2E_APP_ORIGIN);
			await expect(carte.locator('figcaption')).toContainText('© IGN');
			// Beside the head text at 1440 (a 320 px column, decisions of 06/11), under it from 1080 px down.
			const texte = (await page.locator('.fiche-identite').boundingBox())!;
			const boite = (await carte.boundingBox())!;
			expect(Math.round(boite.width)).toBe(320);
			expect(boite.x).toBeGreaterThan(texte.x + texte.width);
			await page.screenshot({ path: preuve(testInfo, `carte-${a.slug}-1440.jpg`), quality: 70, animations: 'disabled' });
			for (const largeur of [1024, 375, 320]) {
				await page.setViewportSize({ width: largeur, height: 800 });
				const t = (await page.locator('.fiche-identite').boundingBox())!;
				const c = (await carte.boundingBox())!;
				expect(c.y, `${largeur} px : la carte passe sous le texte`).toBeGreaterThanOrEqual(t.y + t.height);
				expect(c.x + c.width).toBeLessThanOrEqual(largeur);
				await sansDebordement(page, `carte ${largeur} px`);
			}
			await sansViolationAxe(page, `${a.slug} carte`);
			expectNoUnexpectedOffLocalRequest(page);
		});

		// The sentence moved to the block of « Risques », in the body face: its bold stays an allowed weight.
		test('la phrase des comptes, dans Risques : son gras reste dans les graisses de son texte', async ({ page }) => {
			await page.setViewportSize({ width: 1440, height: 900 });
			await ouvrir(page, a);
			await page.locator('details#q-risques > summary').click();
			const phrase = page.locator('#q-risques .bref-p');
			await expect(phrase).toBeVisible();
			expect(await verifierGraisses(page, '#q-risques .bref-p')).toEqual([]);
			// Every report of the fixture carries a point of vigilance or an outage: the sentence has a bold,
			// so the check above did read one.
			const forts = await phrase.locator('strong').evaluateAll((elements) =>
				elements.map((e) => {
					const style = getComputedStyle(e);
					return `${style.fontFamily.split(',')[0].replace(/["']/g, '').trim()} ${style.fontWeight}`;
				})
			);
			expect(forts.length, 'au moins un gras dans la phrase').toBeGreaterThan(0);
			expect(forts).toEqual(forts.map(() => 'Inter 600'));
		});

		test('carte indisponible : la légende le dit, aucun cadre vide, rien à l’impression', async ({ page }) => {
			await repondreCarteStatique(page, { statut: 503 });
			await page.setViewportSize({ width: 1440, height: 900 });
			await ouvrir(page, a);
			const carte = page.locator('.adresse-carte figure.carte');
			await carte.scrollIntoViewIfNeeded();
			await expect(carte.locator('figcaption')).toContainText('Carte indisponible.');
			await expect(carte.locator('img')).toHaveCount(0);
			// One line of text keeps no column of its own: the head goes back to one column.
			const texte = (await page.locator('.fiche-identite').boundingBox())!;
			expect((await carte.boundingBox())!.y).toBeGreaterThanOrEqual(texte.y + texte.height);
			await page.emulateMedia({ media: 'print' });
			await expect(carte).toBeHidden();
			await page.emulateMedia({ media: 'screen' });
		});

		test('cinq largeurs : les critères servis et eux seuls, aucun débordement, axe sans violation', async ({ page }, testInfo) => {
			test.setTimeout(120_000);
			const servis = a.rapport.donnees.themes.map((t) => t.id).sort();
			for (const largeur of LARGEURS) {
				await page.setViewportSize({ width: largeur, height: largeur >= 1024 ? 900 : 800 });
				await ouvrir(page, a);
				const rendus = await page.locator('li[id^="critere-"]').evaluateAll((d) => d.map((x) => x.getAttribute('data-theme') ?? ''));
				expect(rendus.sort()).toEqual(servis);
				await expect(page.locator('section.bref')).toBeVisible();
				expect(await page.locator('section.bref dl.chiffres-cles > .cle').count(), 'prix, loyer et DPE au plus').toBeLessThanOrEqual(3);
				await expect(page.locator('section.bref dl.chiffres-cles dt', { hasText: 'Année de construction' })).toHaveCount(0);
				const legendHeadings = page.locator('#lire .repli h3');
				await expect(legendHeadings).toHaveCount(2);
				for (const heading of await legendHeadings.all()) {
					await page.locator('#lire .repli').evaluate((repli) => { (repli as HTMLDetailsElement).open = true; });
					await expect.soft(heading, 'legend group heading keeps the compact size of 4.9 at every width').toHaveCSS('font-size', '20px');
				}
				// Every criterion writes its scale next to its value.
				const echelles = await page.locator('li[id^="critere-"] .ligne-ech .ech-mot').allTextContents();
				expect(echelles).toHaveLength(servis.length);
				for (const echelle of echelles) expect(echelle.trim()).not.toBe('');
				await sansDebordement(page, `${largeur} px`);
				// Everything unfolded must not overflow either.
				await page.evaluate(() => document.querySelectorAll('details').forEach((d) => (d.open = true)));
				await sansDebordement(page, `${largeur} px, tout déplié`);
				// 04.8-01 (D-03): no visible text under 14 px, every detail unfolded.
				expect(await verifierPlancherTypo(page, '.public-site'), `plancher de 14 px à ${largeur} px`).toEqual([]);
				await sansViolationAxe(page, `${a.slug} ${largeur} px tout déplié`);
				await page.evaluate(() => document.querySelectorAll('details').forEach((d) => (d.open = false)));
				if (largeur === 1440 || largeur === 375) {
					await page.screenshot({ path: preuve(testInfo, `apres-${a.slug}-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
					await page.screenshot({ path: preuve(testInfo, `apres-${a.slug}-${largeur}-page.jpg`), quality: 70, fullPage: true, animations: 'disabled' });
				}
			}
		});

		test('zoom 200 % (720 × 450) : mise en page mobile, focus visible, panneau utilisable', async ({ page }, testInfo) => {
			await page.setViewportSize({ width: 720, height: 450 });
			await ouvrir(page, a);
			await sansDebordement(page, 'zoom 200 %');
			// Nothing keeps a share of a 450 px screen: the site header does not stick.
			expect(await page.locator('header.site-header').evaluate((h) => getComputedStyle(h).position), 'en-tête non collant').not.toBe('sticky');
			// A criterion reached by the keyboard is never hidden under anything: what is on top of its centre is itself.
			await page.evaluate(() => document.querySelectorAll('details.cat').forEach((d) => ((d as HTMLDetailsElement).open = true)));
			const resume = page.locator('li[id^="critere-"] summary').nth(4);
			await resume.focus();
			await expect(resume).toBeInViewport();
			expect(await resume.evaluate((el) => { const b = el.getBoundingClientRect(); const dessus = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return !!dessus && (dessus === el || el.contains(dessus)); }), 'résumé du critère focalisé non recouvert').toBe(true);
			await sansViolationAxe(page, `${a.slug} zoom 200 %`);
			await page.screenshot({ path: preuve(testInfo, `apres-${a.slug}-zoom200.jpg`), quality: 70, animations: 'disabled' });

			await page.locator('.actions-plus summary').click();
			await page.locator('.adresse-actions').getByRole('link', { name: 'Ventes ressemblantes' }).click();
			const panneau = page.getByRole('dialog', { name: 'Ventes ressemblantes' });
			await expect(panneau).toBeVisible();
			const fermer = panneau.getByRole('button', { name: 'Fermer' });
			await expect(fermer).toBeInViewport();
			await sansViolationAxe(page, `${a.slug} panneau à 200 %`);
			await page.keyboard.press('Escape');
			await expect(panneau).toBeHidden();
		});

		test('« Télécharger (CSV) » : une ligne par critère, aucune clé technique', async ({ page }) => {
			await page.setViewportSize({ width: 1440, height: 900 });
			await ouvrir(page, a);
			const telechargement = page.waitForEvent('download');
			await page.locator('.actions-plus summary').click();
			await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
			await page.getByRole('dialog', { name: 'Exporter les données' }).getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
			const fichier = await telechargement;
			expect(fichier.suggestedFilename()).toBe(`adresse-${a.banId}-export.csv`);
			const csv = (await readFile((await fichier.path())!, 'utf8')).replace(/^\uFEFF/, '');
			const lignes = csv.split(/\r?\n/).filter(Boolean);
			expect(lignes[0]).toBe('Thème,Indicateur,Valeur ou motif,État,Maille,"Période, population, source et réserves"');
			// Header, the identity of the address (Adresse, Commune, Département: 04.7-13, parity with the
			// export of the published fiche 946d5a71), then one row per criterion.
			expect(lignes[1]).toMatch(/^Identité,Adresse,/);
			expect(lignes[2]).toMatch(/^Identité,Commune,/);
			expect(lignes[3]).toMatch(/^Identité,Département,/);
			expect(lignes).toHaveLength(a.rapport.donnees.themes.length + 1 + 3);
			// No technical key: neither the identifiers of the attachments served by this report nor the BAN key.
			const cles = [a.banId, ...a.rapport.donnees.themes.flatMap((t) => (t.rattachement?.cle ? [t.rattachement.cle] : []))];
			for (const cle of cles) expect(csv).not.toContain(cle);
			expect(csv).not.toMatch(/ban_id|field_id/);
		});

		test('impression A4 : toutes les fiches ouvertes, sommaire et actions masqués', async ({ page }, testInfo) => {
			await page.setViewportSize({ width: 1024, height: 900 });
			await ouvrir(page, a);
			await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
			await page.emulateMedia({ media: 'print' });
			const fermees = await page.locator('.adresse details:not([open])').count();
			expect(fermees, 'aucun détail fermé à l’impression').toBe(0);
			await expect(page.getByRole('navigation', { name: 'Sur cette fiche' })).toBeHidden();
			await expect(page.locator('.adresse-actions')).toBeHidden();
			// The map prints with its caption once its images are loaded (UI-SPEC v3 § 12).
			await expect(page.locator('.adresse-carte figure.carte figcaption')).toBeVisible();
			await expect
				.poll(() => page.locator('.adresse-carte img').evaluateAll((imgs) => imgs.map((i) => (i as HTMLImageElement).naturalWidth)))
				.toEqual([448, 448]);
			await page.pdf({ path: preuve(testInfo, `apres-${a.slug}-impression.pdf`), format: 'A4', margin: { top: '14mm', bottom: '14mm', left: '14mm', right: '14mm' } });
			await page.emulateMedia({ media: 'screen' });
			await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
			expect(await page.locator('.adresse details:not([open])').count()).toBeGreaterThan(0);
		});
	});
}
