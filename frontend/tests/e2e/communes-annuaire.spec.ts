import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';

// 04.6-02 — the directory of communes, `/communes` without `q`.
//
// What this spec locks, on the synthetic source under exact expectations (fail-closed journal
// checked after each test, no browser request off the loopback):
//   - without JavaScript: the directory renders server-side, the GET form of filters reaches the
//     API with the same filters (name, population range), the pagination is plain links, and no
//     comparison control is offered (it is a JavaScript enhancement);
//   - with JavaScript: « Ajouter à la comparaison » writes the tab selection and the header counter
//     follows; a filter change is a client navigation to a shareable URL;
//   - a failed price read keeps the list with a notice and its relaunch, never a zero;
//   - at 1440 px a table (caption, headers), at 375 and 320 px one card per commune: no
//     horizontal overflow, 44 px targets, axe WCAG 2.1 AA without violation.
// The departements are arbitrary: nothing in the screen knows which ones the instance hosts.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
const FENETRE = '2024-12-31 → 2025-12-31';
const HEBERGEE = { etat: 'heberge', motif: null, departements: ['35'] };
const HORS = { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] };
const DEFAUT = { couverture: 'servies', sort_by: 'population', order: 'desc', page: '1', per_page: '50', inclure_arrondissements: 'true' };

const RENNES = {
	code_insee: '35238',
	nom: 'Rennes',
	departement: '35',
	population: 227830,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: HEBERGEE,
	prix: {
		maison: { mediane_m2: 4120, effectif: 512, etat: null },
		appartement: { mediane_m2: 3890, effectif: 2210, etat: null }
	}
};
const BRUZ = {
	code_insee: '35047',
	nom: 'Bruz',
	departement: '35',
	population: 19100,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: HEBERGEE,
	prix: {
		maison: { mediane_m2: 3300, effectif: 88, etat: null },
		appartement: {
			mediane_m2: null,
			effectif: 3,
			etat: { etat: 'insufficient_sample', motif: "3 mutations d'appartements retenues sur 12 mois, sous le seuil de publication de 5." }
		}
	}
};
const NIORT = {
	code_insee: '79191',
	nom: 'Niort',
	departement: '79',
	population: 59000,
	population_annee: null,
	population_source: 'geo_communes',
	hebergement: HORS,
	prix: {
		maison: { mediane_m2: null, effectif: null, etat: { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' } },
		appartement: { mediane_m2: null, effectif: null, etat: { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' } }
	}
};

type Reply = { status: number; body: unknown };

function page_(results: unknown[], extra: Record<string, unknown> = {}): Reply {
	return {
		status: 200,
		body: {
			statut: 'ok',
			donnees: {
				results,
				total_count: results.length,
				page: 1,
				per_page: 50,
				has_next: false,
				couverture: 'servies',
				departements_servis: ['35'],
				prix: { etat: 'servi', motif: null, fenetre_mediane_dvf: FENETRE },
				...extra
			}
		}
	};
}

function annuaire(query: Record<string, string>, responses: Reply[]) {
	return {
		method: 'GET',
		pathname: '/v1/communes/annuaire',
		query: { ...DEFAUT, ...query },
		minCalls: responses.length,
		maxCalls: responses.length,
		responses
	};
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
	await page.goto(url);
	await hydrated;
}

const filtres = (page: Page) => page.getByRole('form', { name: 'Filtrer l’annuaire' });
// 04.8-07 (UI-SPEC § 6.5): the filters fold in a native `details` « Filtrer », closed while no filter
// is active. Opening it is a native toggle, with or without JavaScript.
async function ouvrirFiltres(page: Page) {
	const det = page.locator('main details.det');
	await expect(det.locator('summary')).toHaveText('Filtrer');
	if (!(await det.evaluate((el) => (el as HTMLDetailsElement).open))) await det.locator('summary').click();
	await expect(filtres(page)).toBeVisible();
}
const tableau = (page: Page) => page.locator('table.annuaire-table');
const rangee = (page: Page, code: string) => tableau(page).locator(`tr[data-code-insee="${code}"]`);

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

test.describe('Sans JavaScript', () => {
	test.use({ javaScriptEnabled: false, viewport: { width: 1440, height: 900 } });

	test('consultation, filtres et pagination par formulaire GET et liens', async ({ page, request }) => {
		await configure(request, [
			annuaire({}, [page_([RENNES, BRUZ], { total_count: 120, has_next: true })]),
			annuaire({ nom: 'ren', population_min: '10000', population_max: '49999' }, [
				page_([BRUZ], { total_count: 51, has_next: true })
			]),
			annuaire({ nom: 'ren', population_min: '10000', population_max: '49999', page: '2' }, [
				page_([RENNES], { total_count: 51, page: 2 })
			])
		]);

		await page.goto('/communes');

		// D4 (4.9 lot 7, amends UI-SPEC § 6.5): the h1 names the directory, the panel its list.
		await expect(page.getByRole('heading', { level: 1, name: 'Annuaire des communes' })).toBeVisible();
		await expect(page.getByRole('heading', { level: 2, name: 'Liste des communes' })).toBeVisible();
		await expect(tableau(page).locator('caption')).toContainText('Prix médians au m²');
		const rennes = rangee(page, '35238');
		await expect(rennes.getByRole('rowheader')).toHaveText('Rennes');
		await expect(rennes).toContainText(/4\s120\s€\/m²/);
		await expect(rennes).toContainText(/512\sventes/);
		await expect(tableau(page).locator('caption')).toContainText(/ventes retenues \(DVF\) du 31 décembre 2024 au 31 décembre 2025/);
		// Each price keeps its window for a reader of that cell alone (visually hidden).
		// D7 (2026-11-10): the price of the houses is now the first figure of the row. décembre 2024 au 31 décembre 2025/);
		await expect(rangee(page, '35047')).toContainText(/n\.d\. \(3\sventes\)/);
		await expect(page.getByText(/120\scommunes/).first()).toBeVisible();
		// The comparison is a JavaScript enhancement: no dead control in the served HTML.
		await expect(page.getByRole('button', { name: /Ajouter à la comparaison/ })).toHaveCount(0);

		await ouvrirFiltres(page);
		const form = filtres(page);
		await form.getByLabel('Nom de la commune').fill('ren');
		await form.getByLabel('Population').selectOption('10000-49999');
		await form.getByRole('button', { name: 'Afficher' }).click();

		await expect(page).toHaveURL(/\/communes\?/);
		const url = new URL(page.url());
		expect(url.searchParams.get('nom')).toBe('ren');
		expect(url.searchParams.get('population')).toBe('10000-49999');
		await expect(rangee(page, '35047')).toBeVisible();
		// An active filter keeps « Filtrer » open (UI-SPEC § 6.5).
		await expect(page.locator('main details.det')).toHaveAttribute('open', '');
		await expect(form.getByLabel('Nom de la commune')).toHaveValue('ren');

		await page.getByRole('navigation', { name: "Pages de l'annuaire" }).getByRole('link', { name: 'Page suivante' }).click();
		await expect(page).toHaveURL(/page=2/);
		await expect(rangee(page, '35238')).toBeVisible();
		await expect(page.getByRole('navigation', { name: "Pages de l'annuaire" })).toContainText('Page 2 sur 2');
	});
});

test.describe('Page 699 en deux actions au plus (4.9 lot 7, C5)', () => {
	const NATIONAL = { total_count: 34950, has_next: true };
	const pages = (page: Page) => page.getByRole('navigation', { name: "Pages de l'annuaire" });

	for (const javaScriptEnabled of [false, true]) {
		test.describe(javaScriptEnabled ? 'avec JavaScript' : 'sans JavaScript', () => {
			test.use({ javaScriptEnabled, viewport: { width: 375, height: 812 } });
			const aller = (page: Page, url: string) => (javaScriptEnabled ? open(page, url) : page.goto(url));

			test('« Dernière page » : une action, le filtre gardé', async ({ page, request }) => {
				await configure(request, [
					annuaire({ departement: '35' }, [page_([RENNES], NATIONAL)]),
					annuaire({ departement: '35', page: '699' }, [page_([BRUZ], { ...NATIONAL, page: 699, has_next: false })])
				]);
				await aller(page, '/communes?departement=35');
				await pages(page).getByRole('link', { name: 'Dernière page' }).click();
				await expect(page).toHaveURL(/\/communes\?departement=35&page=699$/);
				await expect(pages(page)).toContainText('Page 699 sur 699');
				await expect(pages(page).locator('[aria-current="page"]')).toHaveText('Page 699');
				await expect(pages(page).getByRole('link', { name: 'Dernière page' })).toHaveCount(0);
			});

			test('saisie d’un numéro : deux actions, le voisinage de ±2 pages autour', async ({ page, request }) => {
				await configure(request, [
					annuaire({}, [page_([RENNES], NATIONAL)]),
					annuaire({ page: '350' }, [page_([BRUZ], { ...NATIONAL, page: 350 })])
				]);
				await aller(page, '/communes');
				await pages(page).getByLabel('Aller à la page').fill('350');
				// The focus scrolls the field into view: the click waits for the end of that scroll.
				const aller_ = pages(page).getByRole('button', { name: 'Aller' });
				let hauteur: number | undefined;
				await expect
					.poll(async () => {
						const precedente = hauteur;
						hauteur = (await aller_.boundingBox())?.y;
						return hauteur !== undefined && hauteur === precedente;
					})
					.toBe(true);
				await aller_.click();
				await expect(page).toHaveURL(/\/communes\?page=350$/);
				await expect(pages(page)).toContainText('Page 350 sur 699');
				for (const numero of [348, 349, 351, 352]) {
					await expect(pages(page).getByRole('link', { name: `Page ${numero}`, exact: true })).toHaveAttribute('href', `/communes?page=${numero}`);
				}
				await expect(pages(page).getByRole('link', { name: 'Page 350', exact: true })).toHaveCount(0);
				await expect(pages(page).getByRole('link', { name: 'Première page' })).toHaveAttribute('href', '/communes');
			});
		});
	}

	for (const largeur of [1440, 375, 320]) {
		test(`${largeur} px : page du milieu, cibles de 44 px, sans débordement ni violation axe`, async ({ page, request }) => {
			await configure(request, [annuaire({ page: '350' }, [page_([RENNES], { ...NATIONAL, page: 350 })])]);
			await page.setViewportSize({ width: largeur, height: 900 });
			await open(page, '/communes?page=350');
			for (const cible of await pages(page).locator('a, button, input').all()) {
				const boite = await cible.boundingBox();
				expect(boite?.height, (await cible.textContent()) ?? 'champ').toBeGreaterThanOrEqual(44);
				expect(boite?.width, (await cible.textContent()) ?? 'champ').toBeGreaterThanOrEqual(44);
			}
			await sansDebordement(page, largeur);
			const axe = await new AxeBuilder({ page }).include('nav[aria-label="Pages de l\'annuaire"]').analyze();
			expect(axe.violations.map((v) => v.id)).toEqual([]);
		});
	}
});

test.describe('Avec JavaScript', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('ajout à la comparaison depuis l’annuaire ; le compteur de l’en-tête suit', async ({ page, request }) => {
		await configure(request, [annuaire({}, [page_([RENNES, BRUZ])])]);
		await open(page, '/communes');

		await expect(page.locator('.site-nav-count')).toHaveCount(0);
		const rennes = rangee(page, '35238');
		await rennes.getByRole('button', { name: /Ajouter à la comparaison/ }).click();

		await expect(rennes.getByRole('status')).toHaveText('Ajoutée à la comparaison (1/4) · Ouvrir le comparateur');
		await expect(page.locator('.site-nav-count')).toHaveText('1');
		await expect(rennes.getByRole('link', { name: 'Ouvrir le comparateur' })).toHaveAttribute('href', '/comparer?communes=35238');
		expect(await page.evaluate(() => sessionStorage.getItem('dc:comparaison'))).toBe('["35238"]');
	});

	test('un filtre de couverture est une navigation vers une URL partageable', async ({ page, request }) => {
		await configure(request, [
			annuaire({}, [page_([RENNES])]),
			annuaire({ couverture: 'toutes' }, [page_([RENNES, NIORT], { couverture: 'toutes' })])
		]);
		await open(page, '/communes');

		await ouvrirFiltres(page);
		await filtres(page).getByLabel('Couverture').selectOption('toutes');
		await filtres(page).getByRole('button', { name: 'Afficher' }).click();

		// A GET form sends every field; empty ones read back as defaults, the API query stays exact.
		await expect(page).toHaveURL(/[?&]couverture=toutes(&|$)/);
		expect(new URL(page.url()).pathname).toBe('/communes');
		const niort = rangee(page, '79191');
		await expect(niort).toContainText('Hors couverture · département non hébergé');
		await expect(niort).not.toContainText('€/m²');
		await expect(page.getByText('territoire_hors_perimetre')).toHaveCount(0);
	});

	test('prix indisponibles : la liste reste, un avis et sa relance, jamais un zéro', async ({ page, request }) => {
		const panne = page_([{ ...RENNES, prix: null }], {
			prix: { etat: 'indisponible', motif: 'Synthèse communale temporairement indisponible.', fenetre_mediane_dvf: null }
		});
		await configure(request, [annuaire({}, [panne, page_([RENNES])])]);
		await open(page, '/communes');

		const avis = page.getByRole('alert').filter({ hasText: 'Prix momentanément indisponibles' });
		await expect(avis).toBeVisible();
		await expect(avis).toContainText('Ce n’est pas une absence de ventes');
		await expect(rangee(page, '35238')).toContainText('Prix indisponible');
		await expect(rangee(page, '35238')).not.toContainText('€/m²');

		await avis.getByRole('button', { name: 'Relancer la lecture des prix' }).click();

		await expect(page.getByRole('alert').filter({ hasText: 'Prix momentanément indisponibles' })).toHaveCount(0);
		await expect(rangee(page, '35238')).toContainText(/4\s120\s€\/m²/);
	});
});

for (const largeur of [1440, 375, 320] as const) {
	test(`mise en page à ${largeur} px : ${largeur > 720 ? 'tableau' : 'une carte par commune'}, axe sans violation`, async ({
		page,
		request
	}) => {
		await configure(request, [annuaire({}, [page_([RENNES, BRUZ], { total_count: 120, has_next: true })])]);
		await page.setViewportSize({ width: largeur, height: 900 });
		await open(page, '/communes');
		await page.evaluate(() => document.fonts.ready);

		const cartes = page.locator('.annuaire-cartes');
		if (largeur > 720) {
			await expect(tableau(page)).toBeVisible();
			await expect(cartes).toBeHidden();
			const enTetes = tableau(page).locator('thead th');
			await expect(enTetes).toHaveCount(6);
			// Figures are right-aligned, in tabular figures.
			const alignement = await rangee(page, '35238')
				.locator('td.num')
				.first()
				.evaluate((td) => [getComputedStyle(td).textAlign, getComputedStyle(td).fontVariantNumeric]);
			expect(alignement).toEqual(['right', 'tabular-nums']);
		} else {
			await expect(tableau(page)).toBeHidden();
			const carte = cartes.locator('li[data-code-insee="35047"]');
			await expect(carte).toBeVisible();
			await expect(carte.locator('dt')).toHaveText(['Maisons', 'Appartements']);
			await expect(carte).toContainText(/n\.d\. \(3\sventes\)/);
			for (const cible of await carte.locator('a, button').all()) {
				expect((await cible.boundingBox())!.height, 'cible de 44 px').toBeGreaterThanOrEqual(44);
			}
		}
		// The controls are measured open (04.8-07: folded in « Filtrer »), the summary with them.
		await ouvrirFiltres(page);
		for (const cible of await page.locator('main details.det > summary, .annuaire-filtres :is(input, select, button), .communes-pages a').all()) {
			expect((await cible.boundingBox())!.height, 'contrôle de 44 px').toBeGreaterThanOrEqual(44);
		}

		await sansDebordement(page, largeur);
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
	});
}

// 04.7-09 — the arrondissements of Paris, Lyon and Marseille: asked for (DEFAUT carries
// `inclure_arrondissements`), listed under their commune and never added to it.
test.describe('Arrondissements sous leur commune', () => {
	test.use({ javaScriptEnabled: false, viewport: { width: 1440, height: 900 } });

	const base = { population_annee: 2022, population_source: 'demographie', hebergement: HEBERGEE, prix: RENNES.prix };
	const PARIS = { ...base, code_insee: '75056', nom: 'Paris', departement: '75', population: 2100000, type_zone: 'commune', commune_parent: null };
	const LYON = { ...base, code_insee: '69123', nom: 'Lyon', departement: '69', population: 520000, type_zone: 'commune', commune_parent: null };
	const PARIS_12 = { ...base, code_insee: '75112', nom: 'Paris 12e Arrondissement', departement: '75', population: 140000, type_zone: 'arrondissement', commune_parent: '75056' };

	test('un arrondissement suit sa commune dans le tableau, avec sa fiche propre', async ({ page, request }) => {
		// Served by population: Lyon sits between Paris and its 12th arrondissement.
		await configure(request, [annuaire({}, [page_([PARIS, LYON, PARIS_12])])]);

		await page.goto('/communes');

		const codes = await tableau(page).locator('tbody tr').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-code-insee')));
		expect(codes).toEqual(['75056', '75112', '69123']);
		const sous = rangee(page, '75112');
		// The visible label is short; the accessible name says the city (two « 1er arrondissement »
		// of two cities never share a name).
		await expect(sous.getByRole('rowheader')).toHaveText('12e arrondissement de Paris');
		const lien = sous.getByRole('link', { name: '12e arrondissement de Paris' });
		await expect(lien).toHaveAttribute('href', '/commune/75112');
		await expect(lien.locator('.annuaire-sr')).toHaveText(' de Paris');
		await expect(rangee(page, '75056').getByRole('rowheader')).toHaveText('Paris');
	});
});
