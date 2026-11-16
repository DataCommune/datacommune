import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import {
	DEPARTEMENT_22,
	DEPARTEMENT_29,
	DEPARTEMENT_44,
	DEPARTEMENT_75,
	DEPARTEMENTS_LISTE
} from '../../src/lib/data/__fixtures__/departement';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { verifierPlancherTypo } from './fixtures/quality';

// 04.7-10 — the departement pages: `/departement/[code]` (four cards « value + definition + base »,
// the principal communes, the ways to the communes and to the other departements, the sources) and
// `/departements` (the list grouped by region).
//
// What this spec locks, on the synthetic source under exact expectations (fail-closed journal checked
// after each test, no browser request off the loopback):
//   - 44 (hosted and imported, the houses under the publication threshold): the figures, the states,
//     the canonical, no robots meta, the BreadcrumbList and the Dataset named as the visible title;
//   - 29 (hosted, not imported): « Non renseigné » on every card, `noindex, follow`, no structured data;
//   - 22 (not hosted): the reason of each card, worded, `noindex, follow`, no Dataset;
//   - 75: the principal zones are the arrondissements, each linked to its fiche;
//   - a failed read answers 503 with the served `Retry-After`, the page staying readable and the
//     internal marker never reaching the client;
//   - at 1440, 375 and 320 px: no horizontal overflow, 44 px targets, axe WCAG 2.1 AA without violation.
// The departements are arbitrary: nothing in the pages knows which ones the instance hosts.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
const SOURCES = [
	{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/' },
	{ nom: 'DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0', url: 'https://data.ademe.fr/datasets/dpe-v2-logements-existants' },
	{ nom: 'Recensement', producteur: 'INSEE', licence: 'Licence Ouverte 2.0' }
];

type Reply = { status: number; body: unknown; headers?: Record<string, string> };

function synthese(donnees: unknown): Reply {
	return { status: 200, body: { statut: 'ok', donnees, meta: { source: SOURCES } } };
}

const liste: Reply = { status: 200, body: { statut: 'ok', donnees: DEPARTEMENTS_LISTE } };
const panne = (retryAfter = '45'): Reply => ({ status: 503, body: { detail: 'Service indisponible' }, headers: { 'Retry-After': retryAfter } });

/** One exact expectation of the synthetic source: the call is made `calls` times, no more, no less. */
function attendu(pathname: string, responses: Reply[], calls = responses.length) {
	return { method: 'GET', pathname, query: {}, minCalls: calls, maxCalls: calls, responses };
}

async function configure(request: APIRequestContext, expectations: unknown[]) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } });
	expect(res.ok()).toBe(true);
}

async function expectSourceContractMet(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const response = await page.goto(url);
	await hydrated;
	return response!;
}

const cartes = (page: Page) => page.locator('section#chiffres dl.cles > .cle');
const carte = (page: Page, id: string) => page.locator(`section#chiffres dl.cles > .cle:has([data-carte="${id}"])`);
const jsonLd = (page: Page) => page.locator('head script[type="application/ld+json"]');

async function donneesStructurees(page: Page): Promise<Record<string, any>[]> {
	return (await jsonLd(page).allTextContents()).map((texte) => JSON.parse(texte));
}

/** Where a capture goes: `DC_DEPARTEMENT_EVIDENCE_DIR` when set, else the output folder of the run, never the repository. */
function evidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_DEPARTEMENT_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

async function sansDebordement(page: Page, largeur: number) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow, `aucun débordement horizontal à ${largeur} px`).toBeLessThanOrEqual(0);
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('44 : hébergé et importé, maisons sous le seuil', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('les chiffres, leurs états, l’indexation et les données structurées', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);

		const response = await open(page, '/departement/44');

		expect(response.status()).toBe(200);
		expect(response.headers()['x-dc-etat-page']).toBeUndefined();
		expect(response.headers()['retry-after']).toBeUndefined();
		await expect(page.getByRole('heading', { level: 1, name: 'Loire-Atlantique' })).toBeVisible();
		await expect(page.locator('.dep-overline')).toHaveText('Département 44 · Pays de la Loire');
		await expect(page.locator('.dep-import')).toHaveText('Données importées le 30 octobre 2026.');

		await expect(cartes(page)).toHaveCount(4);
		await expect(carte(page, 'population')).toContainText(/1\s450\s000\shab\./);
		// D3 (2026-11-10): the tile says its count alone; the census year, the months and the reserve of the
		// documents are in the panel and on paper, as on the commune fiche.
		await expect(carte(page, 'population').locator('.cle-note')).toHaveText(/^207\scommunes$/);
		await expect(carte(page, 'appartement')).toContainText(/3\s480\s€\/m²/);
		await expect(carte(page, 'appartement').locator('.cle-note')).toHaveText(/^7\s350\sventes$/);
		await expect(carte(page, 'dpe').locator('.cle-note')).toHaveText(/^157\s100\sdiagnostics de classe connue$/);
		await expect(carte(page, 'dpe')).toContainText(/15,7\s%/);
		// The houses are under the publication threshold: the state, its served count, no number.
		const maisons = carte(page, 'maison');
		await expect(maisons).toContainText('Effectif insuffisant');
		await expect(maisons).toContainText(/Trop peu d'observations \(4\)/);
		await expect(maisons).not.toContainText('€/m²');
		await expect(maisons.locator('[data-state="insufficient_sample"]')).toBeVisible();
		// 4.9 (A9-6): the note of a tile is a short scope; the definition is one click away, in the panel.
		for (const note of await page.locator('section#chiffres .cle-note').all()) {
			expect((await note.textContent())!.trim().split(/\s+/).length).toBeLessThanOrEqual(25);
		}
		await expect(page.locator('section#chiffres .cles')).not.toContainText('du 30 juin 2024 au 30 juin 2025');
		await page.getByRole('button', { name: 'Détail : Prix médian au m² des appartements' }).click();
		const volet = page.getByRole('dialog', { name: 'Prix médian au m² des appartements' });
		await expect(volet).toBeVisible();
		await expect(volet).toContainText('du 30 juin 2024 au 30 juin 2025');
		await page.keyboard.press('Escape');
		await expect(volet).toBeHidden();
		await expect(page.getByRole('button', { name: 'Détail : Prix médian au m² des appartements' })).toBeFocused();

		await expect(page.locator('section#communes-principales > h2')).toHaveText('Communes les plus peuplées');
		await expect(page.locator('table.dc-table tbody th a')).toHaveText(['Nantes', 'Saint-Nazaire', 'Rezé']);
		await expect(page.locator('table.dc-table tbody th a').first()).toHaveAttribute('href', '/commune/44109');
		await expect(page.locator('table.dc-table tbody tr[data-code-insee="44162"] td')).toHaveText(/43\s500\shabitants$/);

		// Indexation: canonical to itself, no robots meta.
		await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute('href', 'https://datacommune.fr/departement/44');
		await expect(page.locator('head meta[name="robots"]')).toHaveCount(0);
		const donnees = await donneesStructurees(page);
		expect(donnees.map((o) => o['@type'])).toEqual(['BreadcrumbList', 'Dataset']);
		const dataset = donnees[1];
		// D1 (2026-11-10): the panel reads « L’essentiel », as on the commune fiche; the Dataset keeps its name.
		await expect(page.locator('section#chiffres > h2')).toHaveText('L’essentiel');
		expect(dataset.name).toBe('Chiffres clés : Loire-Atlantique');
		expect(dataset.description.length).toBeGreaterThanOrEqual(50);
		expect(dataset.description.length).toBeLessThanOrEqual(5000);
		expect(dataset.temporalCoverage).toBe('2024-06-30/2025-06-30');
		expect(dataset.spatialCoverage).toMatchObject({ '@type': 'AdministrativeArea', name: 'Loire-Atlantique', identifier: '44' });
		for (const phrase of [/1\s450\s000\shab\./, /3\s480\s€\/m²/, /15,7\s%/]) expect(dataset.description).toMatch(phrase);
		expect(dataset.description).not.toContain('maisons à');
	});

	// 4.9 (A9-9): paper has no panel, it carries each explanation itself.
	test('impression : les définitions et les bases de chaque tuile sont reprises', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await open(page, '/departement/44');
		const bloc = page.locator('#definitions-impression');
		await expect(bloc).toBeHidden();
		await page.emulateMedia({ media: 'print' });
		await expect(bloc).toBeVisible();
		await expect(bloc).toContainText('Prix médian au m² des appartements');
		await expect(bloc).toContainText('du 30 juin 2024 au 30 juin 2025');
		await expect(page.locator('section#chiffres button').first()).toBeHidden();
		await page.emulateMedia({ media: 'screen' });
	});

	// D1, D2, D3, D4 (inventory of 2026-11-10, validated by Ianis): the head and the tiles of the commune fiche.
	test('tête et tuiles de la fiche commune : bande pâle, prix d’abord, teintes, « i » dans le coin, rien d’étiré', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await open(page, '/departement/44');

		// The colours of the commune fiche, read from the tokens of the site.
		const couleur = (variable: string) =>
			page.evaluate((v) => {
				const sonde = document.createElement('div');
				sonde.style.background = `var(${v})`;
				document.querySelector('.public-site')!.append(sonde);
				const lue = getComputedStyle(sonde).backgroundColor;
				sonde.remove();
				return lue;
			}, variable);

		expect(await page.locator('.dep-tete').evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(await couleur('--color-bande'));
		await expect(page.locator('.dep-tete h1')).toHaveText('Loire-Atlantique');

		const ids = await cartes(page).evaluateAll((tuiles) => tuiles.map((t) => t.querySelector('[data-carte]')!.getAttribute('data-carte')));
		expect(ids).toEqual(['maison', 'appartement', 'dpe', 'population']);
		const prix = await couleur('--color-prix-fond');
		const logement = await couleur('--color-logement-fond');
		const neutre = await couleur('--color-bg');
		const fonds = await cartes(page).evaluateAll((tuiles) => tuiles.map((t) => getComputedStyle(t).backgroundColor));
		expect(fonds).toEqual([prix, prix, logement, neutre]);

		const vides: Record<string, number> = {};
		for (const tuile of await cartes(page).all()) {
			const libelle = (await tuile.locator('dt').textContent()) ?? '';
			const boite = (await tuile.boundingBox())!;
			const bouton = (await tuile.getByRole('button', { name: /^Détail : / }).boundingBox())!;
			// The « i » in the top right corner of the tile, as `Kpi.svelte`.
			expect(Math.abs(bouton.x + bouton.width - (boite.x + boite.width)), libelle).toBeLessThanOrEqual(1);
			expect(Math.abs(bouton.y - boite.y), libelle).toBeLessThanOrEqual(1);
			const bas = await tuile.evaluate((t) =>
				Math.max(...[...t.querySelectorAll('dt, .dep-valeur, .cle-note')].map((el) => el.getBoundingClientRect().bottom))
			);
			vides[libelle] = boite.y + boite.height - bas;
		}
		// Nothing stretches a tile with nothing inside (D4): each tile keeps the height of its own content, the room
		// under its last line is its padding, the same for all four within a pixel.
		const naturel = Math.min(...Object.values(vides));
		for (const [libelle, vide] of Object.entries(vides)) expect(vide - naturel, libelle).toBeLessThan(2);
	});

	// D5 (2026-11-10): the caption of the table is read by assistive technologies and printed, not shown on screen.
	test('la légende du tableau : hors de l’écran, lue par les aides techniques, imprimée', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await open(page, '/departement/44');
		const legende = page.locator('table.dc-table caption');
		await expect(legende).toContainText('Classées par population décroissante');
		expect((await legende.boundingBox())?.width ?? 0).toBeLessThanOrEqual(1);
		await expect(page.getByRole('table', { name: /Classées par population décroissante/ })).toHaveCount(1);
		await page.emulateMedia({ media: 'print' });
		expect((await legende.boundingBox())!.width).toBeGreaterThan(100);
		await page.emulateMedia({ media: 'screen' });
	});

	test('les liens : annuaire, départements de la région, liste, sources', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await open(page, '/departement/44');

		const parcours = page.locator('#parcourir');
		await expect(parcours.getByRole('link', { name: 'Toutes les communes' })).toHaveAttribute('href', '/communes?departement=44');
		await expect(parcours.locator('ul.liste-liens a[href^="/communes"] .lien-n')).toHaveText(/207\scommunes/);
		await expect(parcours.locator('h3 + ul.liste-liens a .lien-t')).toHaveText([/Vendée\s\(85\)/]);
		await expect(parcours.locator('h3 + ul.liste-liens a')).toHaveAttribute('href', '/departement/85');
		await expect(parcours.getByRole('link', { name: 'Tous les départements' })).toHaveAttribute('href', '/departements');
		// The sources with a web address come first, as external links; the one without stays a plain line.
		const liees = page.locator('#sources ul.liste-liens li');
		await expect(liees).toHaveCount(2);
		await expect(liees.locator('.lien-t')).toHaveText(['DVF (Demandes de valeurs foncières)', 'DPE']);
		await expect(liees.first().locator('a')).toHaveAttribute('href', /^https:\/\/www\.data\.gouv\.fr\//);
		await expect(liees.nth(1).locator('a')).toHaveAttribute('href', /^https:\/\/data\.ademe\.fr\//);
		// 04.8-08 (D-31), 4.9: the definitions left the tiles, DGFiP and ADEME are first said here.
		await expect(liees.first().locator('.lien-n')).toHaveText('DGFiP (Direction générale des finances publiques) / Etalab · Licence Ouverte 2.0');
		await expect(liees.nth(1).locator('.lien-n')).toHaveText('ADEME (Agence de la transition écologique) · Licence Ouverte 2.0');
		const sansLien = page.locator('#sources ul.dep-sources li');
		await expect(sansLien).toHaveCount(1);
		await expect(sansLien.locator('.dep-source-nom')).toHaveText('Recensement');
		await expect(sansLien.locator('.dep-source-detail')).toHaveText('· INSEE · Licence Ouverte 2.0');
		await expect(sansLien.locator('a')).toHaveCount(0);
	});

	// Opus P2-2: the space after a linked source was lost (« DVF· DGFiP »). The text tests read
	// the characters, not the layout, so the geometry is read: the producer starts after the link,
	// with room between them, on the same line at 1440 px.
	test('une source liée est séparée de son producteur par un vrai espace, pas par un caractère', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await open(page, '/departement/44');

		// A linked source is one link: its name (.lien-t) and its « Producteur · Licence » (.lien-n) keep at
		// least 4 px between them, side by side or with the note on its own line (ListeLiens wraps it under
		// 720 px), as the former row of name and detail did (Codex 04.8-05-1, P2).
		const liees = page.locator('#sources ul.liste-liens li');
		await expect(liees).toHaveCount(2);
		for (const index of [0, 1]) {
			const titre = (await liees.nth(index).locator('.lien-t').boundingBox())!;
			const note = (await liees.nth(index).locator('.lien-n').boundingBox())!;
			const memeLigne = note.y < titre.y + titre.height;
			const ecart = memeLigne ? note.x - (titre.x + titre.width) : note.y - (titre.y + titre.height);
			expect(ecart, `le nom et le producteur de la source ${index} gardent 4 px entre eux`).toBeGreaterThanOrEqual(memeLigne ? 4 : 0);
		}
		const sansLien = page.locator('#sources ul.dep-sources li');
		const nom = (await sansLien.locator('.dep-source-nom').boundingBox())!;
		const detailSansLien = (await sansLien.locator('.dep-source-detail').boundingBox())!;
		expect(detailSansLien.x - (nom.x + nom.width)).toBeGreaterThanOrEqual(4);
	});

	test('la liste des départements en échec laisse les chiffres et retire seulement les liens de région', async ({ page, request }) => {
		await configure(request, [
			attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]),
			attendu('/v1/departements', [{ status: 503, body: {} }])
		]);
		const response = await open(page, '/departement/44');

		expect(response.status()).toBe(200);
		await expect(cartes(page)).toHaveCount(4);
		await expect(page.locator('#parcourir h3')).toHaveCount(0);
		await expect(page.getByRole('link', { name: 'Toutes les communes' })).toBeVisible();
	});
});

test.describe('Sans JavaScript', () => {
	test.use({ javaScriptEnabled: false, viewport: { width: 1440, height: 900 } });

	test('la page est complète dans le HTML servi : cartes, tableau, liens, données structurées', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await page.goto('/departement/44');

		await expect(cartes(page)).toHaveCount(4);
		await expect(page.locator('table.dc-table tbody tr')).toHaveCount(3);
		await expect(page.getByRole('link', { name: 'Toutes les communes' })).toBeVisible();
		await expect(jsonLd(page)).toHaveCount(2);
	});
});

// 4.9 (A9-9): a panel needs JavaScript; its explanations must not.
test.describe('44 sans JavaScript', () => {
	test.use({ javaScriptEnabled: false, viewport: { width: 375, height: 812 } });

	test('les définitions et les bases restent lisibles', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await page.goto('/departement/44');
		const bloc = page.locator('section#chiffres .dep-sans-js');
		await expect(bloc).toBeVisible();
		await expect(bloc).toContainText('Prix médian au m² des appartements');
		await expect(bloc).toContainText('du 30 juin 2024 au 30 juin 2025');
		await expect(bloc).toContainText('pas une part de logements');
	});
});

test.describe('29 : hébergé, pas encore importé', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('« Non renseigné » partout, noindex, follow, aucune donnée structurée, jamais un zéro', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/29', [synthese(DEPARTEMENT_29)]), attendu('/v1/departements', [liste])]);
		const response = await open(page, '/departement/29');

		expect(response.status()).toBe(200);
		await expect(page.locator('.dep-import')).toHaveText('Aucune donnée n’a encore été importée pour ce département.');
		await expect(page.locator('section#chiffres dl.cles [data-state="not_reported"]')).toHaveCount(4);
		for (const c of await cartes(page).all()) {
			await expect(c).toContainText('Non renseigné');
			await expect(c.locator('.dep-valeur')).not.toContainText(/\d/);
			// 4.9: no base, no note; the definition stays one click away.
			await expect(c.locator('.cle-note')).toHaveCount(0);
			await expect(c.getByRole('button', { name: /^Détail : / })).toBeVisible();
		}
		await expect(page.locator('head meta[name="robots"]')).toHaveAttribute('content', 'noindex, follow');
		await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute('href', 'https://datacommune.fr/departement/29');
		await expect(jsonLd(page)).toHaveCount(0);
		// The list of the principal communes is the referential's: served although nothing is imported.
		await expect(page.locator('table.dc-table tbody th a')).toHaveText(['Brest', 'Quimper']);
	});
});

test.describe('Des chiffres servis, mais un département que l’API ne dit pas indexable', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('noindex, follow et aucune donnée structurée : la décision d’indexer est celle de l’API', async ({ page, request }) => {
		await configure(request, [
			attendu('/v1/departements/44', [synthese({ ...DEPARTEMENT_44, indexable: false })]),
			attendu('/v1/departements', [liste])
		]);
		await open(page, '/departement/44');

		await expect(carte(page, 'appartement')).toContainText(/3\s480\s€\/m²/);
		await expect(page.locator('head meta[name="robots"]')).toHaveAttribute('content', 'noindex, follow');
		await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute('href', 'https://datacommune.fr/departement/44');
		await expect(jsonLd(page)).toHaveCount(0);
	});
});

test.describe('22 : hors couverture', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('chaque carte dit sa raison, en mots ; noindex, follow ; aucun Dataset', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/22', [synthese(DEPARTEMENT_22)]), attendu('/v1/departements', [liste])]);
		const response = await open(page, '/departement/22');

		expect(response.status()).toBe(200);
		await expect(page.locator('.dep-import')).toHaveCount(0);
		for (const c of await cartes(page).all()) {
			await expect(c).toContainText('Hors couverture · département non hébergé');
			await expect(c).toContainText('Ce site ne couvre pas ce département.');
		}
		await expect(page.getByText('territoire_hors_perimetre')).toHaveCount(0);
		await expect(page.locator('head meta[name="robots"]')).toHaveAttribute('content', 'noindex, follow');
		await expect(jsonLd(page)).toHaveCount(0);
		// Nothing of the departement is hosted: the directory link asks for the whole country.
		await expect(page.getByRole('link', { name: 'Toutes les communes' })).toHaveAttribute('href', '/communes?departement=22&couverture=toutes');
	});
});

test.describe('75 : les arrondissements', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('un département d’une seule commune liste ses arrondissements, chacun lié à sa fiche', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/75', [synthese(DEPARTEMENT_75)]), attendu('/v1/departements', [liste])]);
		await open(page, '/departement/75');

		await expect(page.locator('section#communes-principales > h2')).toHaveText('Arrondissements les plus peuplés');
		await expect(page.locator('table.dc-table thead th').first()).toHaveText('Arrondissement');
		const liens = page.locator('table.dc-table tbody th a');
		await expect(liens).toHaveText(['15e arrondissement de Paris', '20e arrondissement de Paris', '12e arrondissement de Paris']);
		await expect(liens.first()).toHaveAttribute('href', '/commune/75115');
		await expect(page.locator('#parcourir ul.liste-liens a[href^="/communes"] .lien-n')).toHaveText(/1\scommune et 20\sarrondissements/);
	});
});

test.describe('Panne', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('la synthèse en échec : la page répond 503 avec le Retry-After servi, lisible, sans marqueur interne', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [panne('45')]), attendu('/v1/departements', [liste])]);
		const response = await page.goto('/departement/44');

		expect(response!.status()).toBe(503);
		expect(response!.headers()['retry-after']).toBe('45');
		expect(response!.headers()['x-dc-etat-page']).toBeUndefined();
		expect(response!.headers()['x-dc-retry-after']).toBeUndefined();
		const alerte = page.getByRole('alert');
		await expect(alerte).toContainText('Les chiffres de ce département sont momentanément indisponibles');
		await expect(alerte.getByRole('button', { name: 'Recharger les chiffres' })).toBeVisible();
		await expect(page.getByRole('heading', { level: 1, name: 'Loire-Atlantique' })).toBeVisible();
		await expect(page.locator('dl.cles')).toHaveCount(0);
		await expect(page.locator('table')).toHaveCount(0);
		await expect(page.getByText(/€\/m²|hab\./)).toHaveCount(0);
		await expect(jsonLd(page)).toHaveCount(0);
	});

	test('une panne sans Retry-After servi : 503 et un délai par défaut', async ({ page, request }) => {
		await configure(request, [
			attendu('/v1/departements/44', [{ status: 503, body: { detail: 'x' } }]),
			attendu('/v1/departements', [liste])
		]);
		const response = await page.goto('/departement/44');

		expect(response!.status()).toBe(503);
		expect(response!.headers()['retry-after']).toBe('60');
	});

	test('un code inconnu est un 404 ; un code mal formé aussi, sans appel à l’API', async ({ page, request }) => {
		await configure(request, [
			attendu('/v1/departements/99', [{ status: 404, body: { detail: 'Département inconnu' } }]),
			attendu('/v1/departements', [liste])
		]);
		const inconnu = await page.goto('/departement/99');
		expect(inconnu!.status()).toBe(404);

		// Reset the journal: a malformed code must not reach the source at all.
		await expectSourceContractMet(request);
		await configure(request, []);
		const malforme = await page.goto('/departement/4A');
		expect(malforme!.status()).toBe(404);
	});
});

test.describe('/departements', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('les départements par région, ceux sans prix disent pourquoi', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements', [liste])]);
		const response = await open(page, '/departements');

		expect(response.status()).toBe(200);
		await expect(page.getByRole('heading', { level: 1, name: 'Départements' })).toBeVisible();
		await expect(page.locator('section.panneau > h2')).toHaveText(['Bretagne', 'Corse', 'Pays de la Loire']);
		await expect(page.locator('a[href="/departement/44"]')).toHaveAttribute('href', '/departement/44');
		await expect(page.locator('a[href="/departement/44"] .lien-n')).toHaveCount(0);
		await expect(page.locator('a[href="/departement/29"] .lien-n')).toHaveText('Non renseigné'); // D13 (2026-11-10): the common label of the state
		await expect(page.locator('a[href="/departement/22"] .lien-n')).toHaveText('Hors couverture · département non hébergé');
		await expect(page.locator('head link[rel="canonical"]')).toHaveAttribute('href', 'https://datacommune.fr/departements');
		await expect(page.locator('head meta[name="robots"]')).toHaveCount(0);
		const donnees = await donneesStructurees(page);
		expect(donnees.map((o) => o['@type'])).toEqual(['BreadcrumbList']);
	});

	// D12 (2026-11-10, form A): the regions flow in columns, three at 1440 px, one at 375 px, each panel whole.
	test('les régions en colonnes : trois à 1440 px, une à 375 px, chaque panneau entier', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements', [liste, liste], 2)]);
		const lire = () =>
			page.locator('.deps-regions > section.panneau').evaluateAll((panneaux) =>
				panneaux.map((p) => {
					const boite = p.getBoundingClientRect();
					return { x: Math.round(boite.left), y: Math.round(boite.top), hauteur: Math.round(boite.height) };
				})
			);
		await open(page, '/departements');
		const larges = await lire();
		expect(new Set(larges.map((b) => b.x)).size).toBe(3);
		expect(new Set(larges.map((b) => b.y)).size, 'every column starts at the same height').toBe(1);
		await page.setViewportSize({ width: 375, height: 812 });
		await page.reload();
		const etroites = await lire();
		expect(new Set(etroites.map((b) => b.x)).size).toBe(1);
		for (let i = 1; i < etroites.length; i += 1) expect(etroites[i].y).toBeGreaterThan(etroites[i - 1].y + etroites[i - 1].hauteur - 1);
	});

	test('la liste en échec répond 503 avec Retry-After et reste lisible', async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements', [panne('30')])]);
		const response = await page.goto('/departements');

		expect(response!.status()).toBe(503);
		expect(response!.headers()['retry-after']).toBe('30');
		await expect(page.getByRole('alert')).toContainText('La liste des départements est momentanément indisponible');
		await expect(page.locator('ul.liste-liens')).toHaveCount(0);
	});

	test('de la liste à un département par navigation côté client, puis retour par le fil d’Ariane', async ({ page, request }) => {
		await configure(request, [
			attendu('/v1/departements', [liste, liste], 2),
			attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)])
		]);
		await open(page, '/departements');

		await page.locator('a[href="/departement/44"]').click();

		await expect(page).toHaveURL(/\/departement\/44$/);
		await expect(page.getByRole('heading', { level: 1, name: 'Loire-Atlantique' })).toBeVisible();
		await expect(cartes(page)).toHaveCount(4);
		await expect(page.getByRole('navigation', { name: "Fil d'Ariane" }).locator('[aria-current="page"]')).toHaveText('Loire-Atlantique');
	});
});

for (const largeur of [1440, 375, 320] as const) {
	test(`mise en page à ${largeur} px : ${largeur > 1080 ? 'quatre cartes en bande' : largeur > 400 ? 'deux colonnes' : 'une carte par ligne'}, axe sans violation`, async ({
		page,
		request
	}, testInfo) => {
		await configure(request, [attendu('/v1/departements/44', [synthese(DEPARTEMENT_44)]), attendu('/v1/departements', [liste])]);
		await page.setViewportSize({ width: largeur, height: 900 });
		await open(page, '/departement/44');
		await page.evaluate(() => document.fonts.ready);

		const colonnes = await page.locator('dl.cles').evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
		// The grid of the tiles: four columns above 1080 px, two from 401 to 1080 px, one at 400 px and under.
		expect(colonnes).toBe(largeur > 1080 ? 4 : largeur > 400 ? 2 : 1);
		for (const cible of await page.locator('.dc-table tbody th a, #parcourir ul.liste-liens a').all()) {
			expect((await cible.boundingBox())!.height, 'cible de 44 px').toBeGreaterThanOrEqual(44);
		}
		// Opus P3-7: in each row, the population is centred on the name of the commune, whether it fits on
		// one line (1440 px) or wraps on two (375 and 320 px).
		const decalages = await page.locator('table.dc-table tbody tr').evaluateAll((lignes) =>
			lignes.map((ligne) => {
				const lien = ligne.querySelector('th a')!.getBoundingClientRect();
				const plage = document.createRange();
				plage.selectNodeContents(ligne.querySelector('td')!);
				const population = plage.getBoundingClientRect();
				return Math.abs(lien.top + lien.height / 2 - (population.top + population.height / 2));
			})
		);
		expect(decalages).toHaveLength(3);
		for (const decalage of decalages) expect(decalage, 'population centrée sur le nom, en px').toBeLessThanOrEqual(3);
		await sansDebordement(page, largeur);
		// 04.8-01 (D-03): no visible text under 14 px.
		expect(await verifierPlancherTypo(page, '.public-site'), `plancher de 14 px à ${largeur} px`).toEqual([]);
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
		await page.screenshot({ path: evidencePath(testInfo, `departement-44-${largeur}.png`), fullPage: true, animations: 'disabled' });
	});

	test(`mise en page de la panne à ${largeur} px : lisible, axe sans violation`, async ({ page, request }) => {
		await configure(request, [attendu('/v1/departements/44', [panne()]), attendu('/v1/departements', [liste])]);
		await page.setViewportSize({ width: largeur, height: 900 });
		await page.goto('/departement/44');

		await expect(page.getByRole('alert')).toBeVisible();
		await sansDebordement(page, largeur);
		expect(await verifierPlancherTypo(page, '.public-site'), `plancher de 14 px à ${largeur} px`).toEqual([]);
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
	});

	test(`/departements à ${largeur} px : sans débordement, cibles de 44 px, axe sans violation`, async ({ page, request }, testInfo) => {
		await configure(request, [attendu('/v1/departements', [liste])]);
		await page.setViewportSize({ width: largeur, height: 900 });
		await open(page, '/departements');
		await page.evaluate(() => document.fonts.ready);

		for (const cible of await page.locator('ul.liste-liens a').all()) {
			expect((await cible.boundingBox())!.height, 'cible de 44 px').toBeGreaterThanOrEqual(44);
		}
		await sansDebordement(page, largeur);
		expect(await verifierPlancherTypo(page, '.public-site'), `plancher de 14 px à ${largeur} px`).toEqual([]);
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
		await page.screenshot({ path: evidencePath(testInfo, `departements-${largeur}.png`), fullPage: true, animations: 'disabled' });
	});
}
