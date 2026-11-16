// Port of the comparator to the direction A (plan 04.8-05, UI-SPEC v4 §§ 3.10, 6.4, 8). Headless runner,
// isolated context, every SSR read served by the local fail-closed source (AGENTS.md). Split on 2026-11-09
// from `portage-departement-comparateur.spec.ts`, test for test; the departement half is
// `portage-departement.spec.ts`.
//
// What this spec locks:
//   - the form of the mock-up: the breadcrumb, the h1 of each nature, the `.cmp` table in row groups, one
//     page link (→) per compared object, the reading note, the two folded panels;
//   - at 1440, 375 and 320 px: axe (WCAG 2.2 AA) without violation, no overflow (not even under the text
//     spacing of WCAG 1.4.12), the 14 px floor, the locked weights, the 60ch measure, no error of the page;
//     a 200 % zoom lays out as a phone.
// Captures at 1440 and 375 px are kept outside the repository (`DC_PORTAGE_EVIDENCE_DIR` when set).
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const LECTURES = 12;

const MOCK_USER = { id: 2, email: 'test@datacommune.fr', name: 'Test User', is_verified: true, is_admin: false, created_at: '2026-01-01T00:00:00Z' };

async function ouvrir(page: Page, chemin: string, largeur: number, hauteur = 900) {
	await page.setViewportSize({ width: largeur, height: hauteur });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const reponse = await page.goto(chemin);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	// C2: the categories of a comparison of communes are folded on arrival. The cases of this spec read
	// their rows: they are unfolded here, as a reader would open them; the fold itself is locked by
	// `comparateur-revue.spec.ts`. No fold exists for the addresses and the DPE documents.
	await page.locator('details.compare-groupe-pli').evaluateAll((plis) => plis.forEach((pli) => ((pli as HTMLDetailsElement).open = true)));
	return reponse;
}

function collecterErreurs(page: Page): string[] {
	const erreurs: string[] = [];
	page.on('pageerror', (err) => erreurs.push(`pageerror: ${err.message}`));
	page.on('console', (message) => {
		if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) erreurs.push(`console: ${message.text()}`);
	});
	return erreurs;
}

function preuve(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_PORTAGE_EVIDENCE_DIR;
	if (!dossier) return testInfo.outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

async function sansDebordement(page: Page, contexte: string) {
	const { large, vue, fautifs } = await page.evaluate(() => {
		const vue = window.innerWidth;
		const fautifs = [...document.querySelectorAll('body *')]
			.filter((el) => el.getBoundingClientRect().right > vue + 1 && ![...el.children].some((c) => c.getBoundingClientRect().right > vue + 1))
			.filter((el) => !el.closest('[role="region"][tabindex="0"]'))
			.slice(0, 5)
			.map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join('.')} → ${Math.round(el.getBoundingClientRect().right)}`);
		return { large: document.documentElement.scrollWidth, vue, fautifs };
	});
	expect(large, `aucun défilement horizontal (${contexte}) : ${fautifs.join(' ; ')}`).toBeLessThanOrEqual(vue);
}

async function sansViolationAxe(page: Page, contexte: string) {
	const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(axe.passes.length, 'axe n’a rien inspecté').toBeGreaterThan(0);
	expect(axe.violations, `${contexte} : ${JSON.stringify(axe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`).toEqual([]);
}

const ESPACEMENT_DU_TEXTE = '*{line-height:1.5 !important;letter-spacing:0.12em !important;word-spacing:0.16em !important}p{margin-bottom:2em !important}';

async function planDesTitres(page: Page) {
	const niveaux = await page.evaluate(() =>
		[...document.querySelectorAll('main h1, main h2, main h3, main h4, main h5, main h6')]
			.filter((h) => h.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true }))
			.map((h) => ({ niveau: Number(h.tagName.slice(1)), texte: (h.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40) }))
	);
	expect(niveaux.filter((h) => h.niveau === 1), 'un seul h1').toHaveLength(1);
	let precedent = 0;
	for (const h of niveaux) {
		expect(h.niveau, `saut de niveau avant « ${h.texte} »`).toBeLessThanOrEqual(precedent + 1);
		precedent = h.niveau;
	}
}

async function qualite(page: Page, contexte: string, largeur: number, testInfo: TestInfo, capture: string) {
	await sansViolationAxe(page, contexte);
	await sansDebordement(page, contexte);
	if (largeur !== 320) {
		await page.screenshot({ path: preuve(testInfo, `${capture}-${largeur}-ecran.jpg`), quality: 70, animations: 'disabled' });
		await page.screenshot({ path: preuve(testInfo, `${capture}-${largeur}-page.jpg`), quality: 70, fullPage: true, animations: 'disabled' });
	}
	expect.soft(await verifierPlancherTypo(page, 'main'), `${contexte} : plancher de 14 px`).toEqual([]);
	expect.soft(await verifierGraisses(page, 'main'), `${contexte} : graisses`).toEqual([]);
	expect.soft(await verifierMesure(page, 'main', ['p']), `${contexte} : mesure`).toEqual([]);
	await page.addStyleTag({ content: ESPACEMENT_DU_TEXTE });
	await sansDebordement(page, `${contexte}, espacement du texte WCAG 1.4.12`);
}

test.beforeEach(async ({ page }) => {
	await page.route('**/auth/me', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USER) }));
});

// ---- Comparator (UI-SPEC § 6.4) ----------------------------------------------------------------------
// The three natures on the frozen served responses of their own specs (03-09, 04-07, 04-10). Every
// load reads its comparison once per navigation; the quality pass reloads, hence `minCalls: 0`.

type Colonnes = { donnees: { communes?: { code_insee: string; nom: string }[]; colonnes?: Record<string, string>[] } };
const lireFixture = (nom: string) =>
	JSON.parse(readFileSync(new URL(`../../src/lib/data/__fixtures__/${nom}`, import.meta.url), 'utf8')) as Record<string, Colonnes>;
const COMMUNES = lireFixture('commune-comparaison.json').deux_communes_hebergees;
const ADRESSES = lireFixture('adresse-comparaison.json').deux_adresses;
const DOCUMENTS = lireFixture('dpe-comparaison.json').ancien_et_actuel;
const CODES_COMMUNES = COMMUNES.donnees.communes!.map((commune) => commune.code_insee);
const NOMS_COMMUNES = COMMUNES.donnees.communes!.map((commune) => commune.nom);
const IDS_ADRESSES = ADRESSES.donnees.colonnes!.map((colonne) => colonne.ban_id);
const NUMEROS = DOCUMENTS.donnees.colonnes!.map((colonne) => colonne.numero);

const NATURES = [
	{
		nature: 'communes',
		chemin: `/comparer?communes=${CODES_COMMUNES.join(',')}`,
		source: attenteQuery('/v1/communes/comparaison', { codes: CODES_COMMUNES.join(',') }, COMMUNES),
		h1: `Comparer ${NOMS_COMMUNES.join(' et ')}`
	},
	{
		nature: 'adresses',
		chemin: `/comparer?adresses=${IDS_ADRESSES.join(',')}`,
		source: attenteQuery('/v1/adresses/comparaison', { ids: IDS_ADRESSES.join(',') }, ADRESSES),
		h1: 'Comparer des adresses'
	},
	{
		nature: 'dpe',
		chemin: `/comparer?dpe=${NUMEROS.join(',')}`,
		source: attenteQuery('/v1/dpe/comparaison', { numeros: NUMEROS.join(',') }, DOCUMENTS),
		// 04.8-08 (D-31): the acronym spelled out at its first occurrence.
		h1: 'Comparer des documents DPE (diagnostic de performance énergétique)'
	}
] as const;

function attenteQuery(pathname: string, query: Record<string, string>, body: unknown): SourceExpectation {
	return { method: 'GET', pathname, query, minCalls: 0, maxCalls: LECTURES, responses: Array.from({ length: LECTURES }, () => ({ status: 200, body })) };
}

const regionComparaison = (page: Page) => page.getByRole('region', { name: 'Tableau de comparaison' });

test.describe('Comparateur — forme de la maquette (UI-SPEC § 6.4)', () => {
	test('communes : fil, h1 « Comparer A et B », sous-titre, tableau .cmp en rowgroups, liens vers les fiches, note, details.det', async ({ page }) => {
		const source = await configureSsrSource(page.request, [NATURES[0].source]);
		await ouvrir(page, NATURES[0].chemin, 1440);
		const fil = page.getByRole('navigation', { name: "Fil d'Ariane" });
		await expect(fil.getByRole('link', { name: 'Accueil' })).toHaveAttribute('href', '/');
		await expect(fil.locator('[aria-current="page"]')).toHaveText('Comparer');
		await expect(page.getByRole('heading', { level: 1, name: NATURES[0].h1, exact: true })).toBeVisible();
		await expect(page.locator('main .fiche-sous')).toHaveText(
			'Valeur par valeur, sans note globale. Ce sont des références communales, pas l’estimation d’une adresse.'
		);
		const region = regionComparaison(page);
		await expect(region).toHaveAttribute('tabindex', '0');
		const table = region.locator('table.cmp');
		await expect(table).toHaveCount(1);
		await expect(table.locator('caption')).toBeAttached();
		const groupes = table.locator('tbody[data-groupe]');
		expect(await groupes.count()).toBeGreaterThan(1);
		for (const groupe of await groupes.all()) {
			await expect(groupe.locator('tr').first().locator('th[scope="rowgroup"][colspan]')).toHaveCount(1);
			expect(await groupe.locator('th[scope="row"]').count()).toBeGreaterThan(0);
		}
		await expect(page.locator('main .cmp-note')).toContainText('jamais zéro');
		const liens = page.locator('main .cmp-liens ul.liste-liens a');
		await expect(liens).toHaveCount(2);
		expect(await liens.evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(CODES_COMMUNES.map((code) => `/commune/${code}`));
		for (const lien of await liens.all()) await expect(lien.locator('.fleche')).toHaveText('→');
		await expect(page.locator('details.det.comparer-modifier > summary')).toContainText('Modifier la sélection');
		await expect(page.locator('details.det.comparer-lecture > summary')).toContainText('Référence des écarts');
		await planDesTitres(page);
		await source.assert();
	});

	for (const nature of NATURES.slice(1)) {
		test(`${nature.nature} : h1 de la nature gardé, même tableau .cmp, un lien par objet comparé`, async ({ page }) => {
			const source = await configureSsrSource(page.request, [nature.source]);
			await ouvrir(page, nature.chemin, 1440);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(nature.h1);
			await expect(regionComparaison(page).locator('table.cmp')).toHaveCount(1);
			await expect(page.locator('main .cmp-liens ul.liste-liens a')).toHaveCount(2);
			await expect(page.locator('details.det.comparer-modifier')).toHaveCount(1);
			await planDesTitres(page);
			await source.assert();
		});
	}

	// Declared change (direction A on a phone, D5, 2026-11-10): two communes are read stacked up to 720 px
	// and their table no longer scrolls (comparateur-lisibilite.spec.ts). The scrolling table is measured
	// on the addresses, which keep it; the assertions are unchanged.
	test('320 px : le tableau défile dans son enveloppe, jamais la page', async ({ page }) => {
		await configureSsrSource(page.request, [NATURES[1].source]);
		await ouvrir(page, NATURES[1].chemin, 320, 800);
		const region = regionComparaison(page);
		const { defile, deborde } = await region.evaluate((el) => ({
			defile: el.scrollWidth > el.clientWidth,
			deborde: document.documentElement.scrollWidth > window.innerWidth
		}));
		expect(defile, 'le tableau défile dans sa région').toBe(true);
		expect(deborde, 'la page ne défile pas horizontalement').toBe(false);
	});
});

test.describe('Comparateur — qualité aux trois largeurs (UI-SPEC § 8)', () => {
	for (const largeur of LARGEURS) {
		test(`${largeur} px : trois natures`, async ({ page }, testInfo) => {
			test.setTimeout(180_000);
			const erreurs = collecterErreurs(page);
			for (const nature of NATURES) {
				await configureSsrSource(page.request, [nature.source]);
				await ouvrir(page, nature.chemin, largeur, largeur >= 1024 ? 900 : 800);
				await qualite(page, `${nature.chemin} à ${largeur} px`, largeur, testInfo, `comparer-${nature.nature}`);
				// Folded details opened: the selection panel and the reading note do not overflow either.
				await page.reload();
				await page.locator('details.comparer-modifier > summary').click();
				await page.locator('details.comparer-lecture > summary').click();
				await sansDebordement(page, `${nature.chemin} à ${largeur} px, détails ouverts`);
				// The text spacing of WCAG 1.4.12 again, the reload dropped it (Codex 04.8-05-2, P2).
				await page.addStyleTag({ content: ESPACEMENT_DU_TEXTE });
				await sansDebordement(page, `${nature.chemin} à ${largeur} px, détails ouverts, espacement du texte WCAG 1.4.12`);
				await sansViolationAxe(page, `${nature.chemin} à ${largeur} px, détails ouverts`);
			}
			expect(erreurs).toEqual([]);
		});
	}

	test('zoom 200 % (720 × 450) : aucun débordement, le tableau défile dans sa région', async ({ page }) => {
		await configureSsrSource(page.request, [NATURES[0].source]);
		await ouvrir(page, NATURES[0].chemin, 720, 450);
		await sansDebordement(page, 'zoom 200 %');
		await sansViolationAxe(page, 'zoom 200 %');
	});
});
