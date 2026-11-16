import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Phase 03-04 (COM-01, D-42..D-45, D-49) — commune search, its results page and the comparison
// selection.
//
// What this spec locks:
//   - every search reaches this instance's API only (`/v1/communes/search`, answered by the
//     synthetic source under exact expectations: the SSR results page and the browser combobox
//     alike); no browser request leaves the local environment (D-42);
//   - two homonyms stay two cards (h2), told apart by postal code, departement and INSEE code;
//     the one out of coverage keeps its card with the badge and the reason served by the API,
//     and its fiche link is live (D-43, D-44). The departements are arbitrary: nothing in the
//     screen knows which ones the instance hosts (D-34, D-35);
//   - the combobox: arrows move aria-activedescendant, Escape closes then clears, Tab leaves
//     without choosing, Enter on « Voir tous les résultats » opens the results page; the
//     announcement is agreed in number; the departement filter is kept while typing and carried
//     to the results page; « Aucun résultat » offers the filter removal; an outage offers
//     « Relancer la recherche » and is never shown as an empty result;
//   - a postal code outside the departements where the BAN is hosted is rendered as out of
//     coverage with its reason, never as « Aucune commune trouvée », and a postal code without
//     address in the hosted departements is a zero naming the departements served (CR-01);
//   - « Ajouter à la comparaison » confirms in place and the header counter follows the tab
//     selection (absent at 0, 1 then 2), and a fifth commune is refused with the contract text;
//   - at 375 px the cards stack, the document does not overflow and the listbox takes the width
//     of the field; axe WCAG 2.1 AA finds no violation, list open or not.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

type Hosting = {
	etat: 'heberge' | 'hors_couverture';
	motif: string | null;
	departements: string[];
};

const HOSTED: Hosting = { etat: 'heberge', motif: null, departements: ['35'] };
const OUTSIDE: Hosting = {
	etat: 'hors_couverture',
	motif: 'territoire_hors_perimetre',
	departements: []
};

const LE_PIN_79 = {
	code_insee: '79211',
	nom: 'Le Pin',
	code_postal: null,
	departement: '79',
	population: 1000,
	population_annee: null,
	population_source: 'geo_communes',
	hebergement: OUTSIDE
};
const LE_PIN_35 = {
	code_insee: '35999',
	nom: 'Le Pin',
	code_postal: '35170',
	departement: '35',
	population: 750,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: HOSTED
};

type Reply = { status: number; body: unknown };
type Expectation = {
	method: string;
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

const ok = (donnees: unknown[]): Reply => ({ status: 200, body: { statut: 'ok', donnees } });
const okAvecMeta = (donnees: unknown[], hebergement: Record<string, Hosting>): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees, meta: { hebergement } }
});

// Glossary wording, never the outage one (03-17 T-7, V14); the live region announces the title (A12).
const TITRE_HORS_COUVERTURE = 'Code postal hors couverture';

function search(query: Record<string, string>, replies: Reply[]): Expectation {
	return {
		method: 'GET',
		pathname: '/v1/communes/search',
		query: { ...query, inclure_arrondissements: 'true' },
		minCalls: replies.length,
		maxCalls: replies.length,
		responses: replies
	};
}

// Since 04.6-02 `/communes` without `q` is the directory: its server-side load reads the default page
// once. The scenarios opened there are about the search field: the directory answers an empty page.
const ANNUAIRE_PAR_DEFAUT: Expectation = {
	method: 'GET',
	pathname: '/v1/communes/annuaire',
	query: { couverture: 'servies', sort_by: 'population', order: 'desc', page: '1', per_page: '50', inclure_arrondissements: 'true' },
	minCalls: 1,
	maxCalls: 1,
	responses: [
		{
			status: 200,
			body: {
				statut: 'ok',
				donnees: {
					results: [],
					total_count: 0,
					page: 1,
					per_page: 50,
					has_next: false,
					couverture: 'servies',
					departements_servis: ['35'],
					prix: { etat: 'sans_lecture', motif: null, fenetre_mediane_dvf: null }
				}
			}
		}
	]
};

// Since 04-17 the field of `/` and `/communes` and the results page are the single search (D-106):
// each search of communes (`limit: '8'` in the field, the page size on `/communes?q=`) runs with
// the address search of the same query and filter (`limite: '8'`). These scenarios are about
// communes: the address search answers, in step, nothing in a hosted BAN (or the same failure
// status), so the communes contract of each test is unchanged.
const ADRESSES_SANS_RESULTAT: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			ordre_types: ['commune', 'adresse'],
			resultats: [],
			hebergement: { ban: HOSTED }
		}
	}
};

function adressesEnMiroir(communes: Expectation): Expectation | null {
	if (communes.pathname !== '/v1/communes/search') return null;
	const { limit: _limit, inclure_arrondissements: _arrondissements, ...query } = communes.query;
	return {
		...communes,
		pathname: '/v1/adresses/recherche',
		query: { ...query, limite: '8' },
		responses: communes.responses.map((reply) =>
			reply.status >= 400 ? reply : ADRESSES_SANS_RESULTAT
		)
	};
}

/** One expectation per address query: the field and the results page may ask the same one. */
function fusionner(miroirs: Expectation[]): Expectation[] {
	const parRequete = new Map<string, Expectation>();
	for (const miroir of miroirs) {
		const cle = JSON.stringify(Object.entries(miroir.query).sort());
		const deja = parRequete.get(cle);
		parRequete.set(
			cle,
			deja
				? {
						...deja,
						minCalls: deja.minCalls + miroir.minCalls,
						maxCalls: deja.maxCalls + miroir.maxCalls,
						responses: [...deja.responses, ...miroir.responses]
					}
				: miroir
		);
	}
	return [...parRequete.values()];
}

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	const miroirs = fusionner(
		expectations.map(adressesEnMiroir).filter((e): e is Expectation => e !== null)
	);
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: [...expectations, ...miroirs] }
	});
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
	const hydrated = page.waitForResponse(
		(response) => new URL(response.url()).pathname === '/auth/me'
	);
	await page.goto(url);
	await hydrated;
}

// 04.8-07 (UI-SPEC § 6.5): one commune of the results is a row `li.resultat`, a single whole-row link
// to its fiche, its out-of-coverage reason and « Ajouter à la comparaison » under it (was a card).
function card(page: Page, code: string) {
	return page.locator(`li.resultat[data-code-insee="${code}"]`);
}

// The single field of `/` and `/communes` (04-17, UI-SPEC « Champ de `/` »).
function searchForm(page: Page) {
	return page.getByRole('search', { name: 'Rechercher un lieu' });
}

// 04.8-07: `/` and `/communes` carry the large field (UI-SPEC § 3.13, § 6.5, § 6.6), labelled
// « Adresse, commune ou code postal ».
function combobox(page: Page) {
	return page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
}

function listOptions(page: Page) {
	return page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option');
}

function comparerLink(page: Page) {
	return page
		.getByRole('navigation', { name: 'Navigation principale' })
		.getByRole('link', { name: /^Comparer/ });
}

async function activeOptionId(page: Page, index: number) {
	return (await listOptions(page).nth(index).getAttribute('id')) ?? '<no id>';
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('Résultats de recherche — homonymes et hors couverture', () => {
	test('deux homonymes, deux cartes ; la commune hors couverture reste listée avec son motif', async ({
		page,
		request
	}) => {
		await configure(request, [search({ q: 'le pin', limit: '20' }, [ok([LE_PIN_79, LE_PIN_35])])]);

		await page.goto('/communes?q=le+pin');

		// The typed group « Communes » (04-17), then one row per homonym (04.8-07, UI-SPEC § 6.5: the name
		// is the title of a whole-row link, no longer an h2 of a card).
		const headings = page.getByRole('heading', { level: 2 });
		await expect(headings).toHaveText(['Communes']);
		await expect(page.locator('li.resultat .lien-t')).toHaveText(['Le Pin', 'Le Pin']);

		const outside = card(page, '79211');
		const hosted = card(page, '35999');
		await expect(outside).toContainText('Deux-Sèvres (79) · INSEE 79211');
		await expect(hosted).toContainText('35170 · Ille-et-Vilaine (35) · INSEE 35999');
		// H6: the year is shown only when the API serves it.
		await expect(hosted).toContainText('750 habitants · 2022 · INSEE');
		await expect(outside.getByText(/habitants/)).toHaveText('1 000 habitants');

		await expect(outside.getByText('Hors couverture · département non hébergé')).toBeVisible();
		await expect(outside).toContainText('Ce site ne couvre pas ce département.');
		await expect(hosted.getByText(/Hors couverture/)).toHaveCount(0);
		// The technical key never reaches the screen.
		await expect(page.getByText('territoire_hors_perimetre')).toHaveCount(0);

		// Listed, never greyed out: its fiche opens like any other. 04.8-07 (UI-SPEC § 6.5): the name link
		// and « Ouvrir la fiche » are one whole-row link, the only link of the row.
		await expect(outside.getByRole('link')).toHaveCount(1);
		for (const link of [outside.getByRole('link', { name: /^Le Pin/ }), outside.locator('a.resultat-lien')]) {
			await expect(link).toHaveAttribute('href', '/commune/79211');
			await expect(link).not.toHaveAttribute('aria-disabled', 'true');
		}
		await expect(hosted.locator('a.resultat-lien')).toHaveAttribute(
			'href',
			'/commune/35999'
		);
	});

	test('aucun résultat : le contrat, puis le retrait du filtre garde la saisie', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: 'zzz', departement: '79', limit: '20' }, [ok([])]),
			search({ q: 'zzz', limit: '20' }, [ok([])])
		]);

		await open(page, '/communes?q=zzz&departement=79');

		const results = page.getByRole('region', { name: 'Résultats' });
		// D8 (2026-11-10, agreed by the orchestrator): the « Aucun résultat » is the Notice of the fiche; its
		// title is its h2, announced by the status role of the panel (was the state line `p.r-etat`). Read by role.
		const etat = results.getByRole('status').getByRole('heading', { level: 2 });
		await expect(etat).toHaveText('Aucune commune ni adresse trouvée pour « zzz »');
		await expect(etat).toBeVisible();
		await expect(results).toContainText("Cela ne veut pas dire qu'elle n'existe pas.");
		// A zero stays neutral: out of coverage alone has the warn look (V14).
		await expect(results.locator('.communes-notice')).not.toHaveClass(/communes-notice-warn/);

		await results.getByRole('button', { name: 'Retirer le filtre département' }).click();

		await expect(page).toHaveURL(/\/communes\?q=zzz$/);
		await expect(etat).toHaveText('Aucune commune ni adresse trouvée pour « zzz »');
		await expect(etat).toBeVisible();
		await expect(results.getByRole('button', { name: 'Retirer le filtre département' })).toHaveCount(
			0
		);
	});

	test('code postal hors des départements où la BAN est hébergée : l’état sur la page de résultats', async ({
		page,
		request
	}) => {
		await configure(request, [search({ q: '33000', limit: '20' }, [okAvecMeta([], { ban: OUTSIDE })])]);

		await open(page, '/communes?q=33000');

		const results = page.getByRole('region', { name: 'Résultats' });
		await expect(results.getByRole('heading', { level: 2 })).toHaveText(TITRE_HORS_COUVERTURE);
		// Same look as the out-of-coverage notice of the fiche, and its coverage link (V14, V15).
		await expect(results.locator('.communes-notice')).toHaveClass(/communes-notice-warn/);
		await expect(results.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute(
			'href',
			'/datasets'
		);
		await expect(results).toContainText(
			'Ce site ne couvre pas ce département. Cherchez la commune par son nom.'
		);
		await expect(results.getByText(/Aucune commune trouvée/)).toHaveCount(0);
		await expect(page.getByText('territoire_hors_perimetre')).toHaveCount(0);
		await expect(page).toHaveTitle(/^Résultats pour «\u202f33000\u202f»/);
	});

	test('code postal sans adresse dans les départements hébergés : zéro borné, filtre retirable', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: '35999', departement: '35', limit: '20' }, [okAvecMeta([], { ban: HOSTED })]),
			search({ q: '35999', limit: '20' }, [okAvecMeta([], { ban: HOSTED })])
		]);

		await open(page, '/communes?q=35999&departement=35');

		const results = page.getByRole('region', { name: 'Résultats' });
		// D8 (2026-11-10): the title of the Notice, in its status region (was the state line `p.r-etat`).
		const titre = results.getByRole('status').getByRole('heading', { level: 2 });
		await expect(titre).toHaveText('Aucune commune ni adresse trouvée pour « 35999 »');
		await expect(titre).toBeVisible();
		await expect(results).toContainText(
			'La recherche par code postal porte sur : Ille-et-Vilaine (35).'
		);

		await results.getByRole('button', { name: 'Retirer le filtre département' }).click();

		await expect(page).toHaveURL(/\/communes\?q=35999$/);
		await expect(titre).toHaveText('Aucune commune ni adresse trouvée pour « 35999 »');
		await expect(titre).toBeVisible();
		await expect(results).toContainText(
			'La recherche par code postal porte sur : Ille-et-Vilaine (35).'
		);
		await expect(results.getByText(TITRE_HORS_COUVERTURE)).toHaveCount(0);
	});

	test('panne : jamais un résultat vide ; « Relancer la recherche » rejoue la même recherche', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: 'le pin', limit: '20' }, [
				{ status: 503, body: { detail: 'indisponible' } },
				ok([LE_PIN_35])
			])
		]);

		await open(page, '/communes?q=le+pin');

		const results = page.getByRole('region', { name: 'Résultats' });
		await expect(
			results.getByText('La recherche est momentanément indisponible. Réessayez dans un instant.')
		).toBeVisible();
		await expect(results.getByText(/Aucune commune trouvée/)).toHaveCount(0);

		await results.getByRole('button', { name: 'Relancer la recherche' }).click();

		await expect(card(page, '35999')).toBeVisible();
		// D11 (2026-11-10): the count is said once, by the state line; the panel « Communes » no longer repeats it.
		await expect(results.getByRole('status').first()).toHaveText('1 commune proposée');
	});

	test('« Afficher plus » garde la recherche et le filtre', async ({ page, request }) => {
		const vingt = Array.from({ length: 20 }, (_, index) => ({
			...LE_PIN_35,
			code_insee: `35${String(100 + index)}`,
			nom: `Saint-Test ${index + 1}`
		}));
		await configure(request, [search({ q: 'saint', departement: '35', limit: '20' }, [ok(vingt)])]);

		await page.goto('/communes?q=saint&departement=35');

		// 04.8-07 (UI-SPEC § 6.5): the group is a simple panel, each commune a row `li.resultat`.
		await expect(page.locator('main section.panneau-simple > h2')).toHaveText(['Communes']);
		await expect(page.locator('li.resultat[data-code-insee]')).toHaveCount(20);
		await expect(page.getByRole('link', { name: 'Afficher plus' })).toHaveAttribute(
			'href',
			'/communes?q=saint&departement=35&limite=40'
		);
	});
});

test.describe('Champ de recherche — combobox', () => {
	test('huit options gardent l’ordre API et une saisie remplacée avant départ est annulée', async ({
		page,
		request
	}) => {
		const huit = Array.from({ length: 8 }, (_, index) => ({
			...LE_PIN_35,
			code_insee: `35${String(200 + index)}`,
			nom: `Commune ordre ${index + 1}`
		}));
		await configure(request, [search({ q: 'nouveau', limit: '8' }, [ok(huit)])]);
		await open(page, '/');

		const input = combobox(page);
		await input.fill('ancien');
		// Both input events happen before the component debounce: the obsolete request must never leave.
		await input.fill('nouveau');

		await expect(listOptions(page)).toHaveCount(9);
		await expect(listOptions(page).locator('.cs-option-name')).toHaveText(
			huit.map((commune) => commune.nom)
		);
		await expect(listOptions(page).last()).toHaveText('Voir tous les résultats pour « nouveau »');
		await expect(searchForm(page).getByRole('status')).toHaveText('8 communes proposées');
	});

	// UI-003 of the 2026-10-24 UI review: past the 7th arrow the active option left the 360 px list box.
	test('clavier : l’option active reste visible dans la liste qui défile, sans faire défiler la page', async ({
		page,
		request
	}) => {
		const huit = Array.from({ length: 8 }, (_, index) => ({
			...LE_PIN_35,
			code_insee: `35${String(300 + index)}`,
			nom: `Commune clavier ${index + 1}`
		}));
		await configure(request, [search({ q: 'clavier', limit: '8' }, [ok(huit)])]);
		await open(page, '/');

		const input = combobox(page);
		await input.fill('clavier');
		await expect(listOptions(page)).toHaveCount(9);
		const listbox = page.getByRole('listbox', { name: 'Lieux proposés' });
		expect(
			await listbox.evaluate((box) => box.scrollHeight > box.clientHeight),
			'neuf options dépassent la hauteur de la liste'
		).toBe(true);
		const pageAvant = await page.evaluate(() => scrollY);

		for (let index = 0; index < 9; index += 1) {
			await input.press('ArrowDown');
			const activeId = await activeOptionId(page, index);
			await expect(input).toHaveAttribute('aria-activedescendant', activeId);
			const visible = await listbox.evaluate((box, id) => {
				const option = document.getElementById(id);
				if (!option) return false;
				const boite = box.getBoundingClientRect();
				const cible = option.getBoundingClientRect();
				return cible.top >= boite.top - 1 && cible.bottom <= boite.bottom + 1;
			}, activeId);
			expect(visible, `option ${index + 1} visible dans la liste`).toBe(true);
			await expect(input).toBeFocused();
		}
		expect(await page.evaluate(() => scrollY), 'la page ne défile pas').toBe(pageAvant);
	});

	test('clavier : flèches, Échap puis vidage, Tab sans choix, Entrée sur « Voir tous les résultats »', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: 'le pin', limit: '8' }, [
				ok([LE_PIN_79, LE_PIN_35]),
				ok([LE_PIN_79, LE_PIN_35]),
				ok([LE_PIN_79, LE_PIN_35])
			]),
			search({ q: 'le pin', limit: '20' }, [ok([LE_PIN_79, LE_PIN_35])]),
			ANNUAIRE_PAR_DEFAUT
		]);
		await open(page, '/communes');
		const input = combobox(page);
		const status = searchForm(page).getByRole('status');

		await input.fill('le pin');
		await expect(listOptions(page)).toHaveCount(3);
		await expect(status).toHaveText('2 communes proposées');
		await expect(input).toHaveAttribute('aria-expanded', 'true');

		await input.press('ArrowDown');
		await expect(input).toHaveAttribute('aria-activedescendant', await activeOptionId(page, 0));
		await input.press('ArrowDown');
		await expect(input).toHaveAttribute('aria-activedescendant', await activeOptionId(page, 1));
		await input.press('ArrowUp');
		await expect(input).toHaveAttribute('aria-activedescendant', await activeOptionId(page, 0));

		await input.press('Escape');
		await expect(listOptions(page)).toHaveCount(0);
		await expect(input).toHaveAttribute('aria-expanded', 'false');
		await expect(input).toHaveValue('le pin');
		await input.press('Escape');
		await expect(input).toHaveValue('');

		await input.fill('le pin');
		await expect(listOptions(page)).toHaveCount(3);
		await input.press('ArrowDown');
		await input.press('Tab');
		await expect(listOptions(page)).toHaveCount(0);
		await expect(page).toHaveURL(/\/communes$/);

		// A new text, typed from an empty field: the third request.
		await input.fill('');
		await input.fill('le pin');
		await expect(listOptions(page)).toHaveCount(3);
		await input.press('ArrowUp');
		await expect(input).toHaveAttribute('aria-activedescendant', await activeOptionId(page, 2));
		await expect(listOptions(page).nth(2)).toHaveText('Voir tous les résultats pour « le pin »');
		await input.press('Enter');

		await expect(page).toHaveURL(/\/communes\?q=le\+pin$/);
		// 04.8-07 (UI-SPEC § 6.5): one h2 for the group, one row per homonym.
		await expect(page.getByRole('heading', { level: 2 })).toHaveText(['Communes']);
		await expect(page.locator('li.resultat .lien-t')).toHaveText(['Le Pin', 'Le Pin']);
	});

	test('le filtre département est gardé pendant la saisie et dans « Voir tous les résultats »', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: 'le pin', departement: '79', limit: '8' }, [ok([LE_PIN_79])]),
			search({ q: 'le pin', departement: '79', limit: '20' }, [ok([LE_PIN_79])]),
			ANNUAIRE_PAR_DEFAUT
		]);
		await open(page, '/communes');

		await searchForm(page).getByRole('combobox', { name: 'Département' }).selectOption('79');
		await combobox(page).fill('le pin');

		await expect(listOptions(page)).toHaveCount(2);
		await expect(searchForm(page).getByRole('status')).toHaveText('1 commune proposée');
		await expect(listOptions(page).first()).toContainText('Deux-Sèvres (79)');
		await expect(listOptions(page).first()).toContainText('Hors couverture');

		await listOptions(page).last().click();

		await expect(page).toHaveURL(/\/communes\?q=le\+pin&departement=79$/);
		// D11 (2026-11-10): the count is said once, by the state line above the groups.
		await expect(page.getByRole('region', { name: 'Résultats' }).getByRole('status').first()).toHaveText('1 commune proposée');
		await expect(searchForm(page).getByRole('combobox', { name: 'Département' })).toHaveValue('79');
	});

	test('aucune suggestion : contrat annoncé, retrait du filtre, « Voir tous les résultats » gardé', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: 'zzz', departement: '79', limit: '8' }, [ok([])]),
			search({ q: 'zzz', limit: '8' }, [ok([])]),
			ANNUAIRE_PAR_DEFAUT
		]);
		await open(page, '/communes');
		const form = searchForm(page);

		await searchForm(page).getByRole('combobox', { name: 'Département' }).selectOption('79');
		await combobox(page).fill('zzz');

		await expect(form.getByText('Aucune commune ni adresse trouvée pour « zzz »')).toBeVisible();
		await expect(form).toContainText("Cela ne veut pas dire qu'elle n'existe pas.");
		await expect(form.locator('.cs-notice')).not.toHaveClass(/cs-notice-warn/);
		await expect(listOptions(page)).toHaveText(['Voir tous les résultats pour « zzz »']);

		await form.getByRole('button', { name: 'Retirer le filtre département' }).click();

		await expect(searchForm(page).getByRole('combobox', { name: 'Département' })).toHaveValue('');
		await expect(form.getByRole('button', { name: 'Retirer le filtre département' })).toHaveCount(
			0
		);
		await expect(form.getByText('Aucune commune ni adresse trouvée pour « zzz »')).toBeVisible();
	});

	test('code postal hors des départements où la BAN est hébergée : l’état, jamais un résultat vide', async ({
		page,
		request
	}) => {
		await configure(request, [search({ q: '33000', limit: '8' }, [okAvecMeta([], { ban: OUTSIDE })])]);
		await open(page, '/');
		const form = searchForm(page);

		await combobox(page).fill('33000');

		// The live region announces the same title (A12): the notice is read by its own title line.
		await expect(form.locator('.cs-notice-title')).toHaveText(TITRE_HORS_COUVERTURE);
		await expect(form.locator('.cs-notice-title')).toBeVisible();
		await expect(form.locator('.cs-notice')).toHaveClass(/cs-notice-warn/);
		await expect(form.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute(
			'href',
			'/datasets'
		);
		await expect(form).toContainText(
			'Ce site ne couvre pas ce département. Cherchez la commune par son nom.'
		);
		await expect(form.getByText(/Aucune commune trouvée/)).toHaveCount(0);
		await expect(page.getByText('territoire_hors_perimetre')).toHaveCount(0);
		await expect(form.getByRole('status')).toHaveText(TITRE_HORS_COUVERTURE);
		await expect(listOptions(page)).toHaveText(['Voir tous les résultats pour « 33000 »']);
	});

	test('code postal sans adresse dans les départements hébergés : zéro borné qui nomme les départements servis', async ({
		page,
		request
	}) => {
		await configure(request, [search({ q: '35999', limit: '8' }, [okAvecMeta([], { ban: HOSTED })])]);
		await open(page, '/');
		const form = searchForm(page);

		await combobox(page).fill('35999');

		await expect(form.getByText('Aucune commune ni adresse trouvée pour « 35999 »')).toBeVisible();
		await expect(form).toContainText(
			'La recherche par code postal porte sur : Ille-et-Vilaine (35).'
		);
		await expect(form.getByText(TITRE_HORS_COUVERTURE)).toHaveCount(0);
	});

	test('panne : message du contrat et « Relancer la recherche »', async ({ page, request }) => {
		await configure(request, [
			search({ q: 'le pin', limit: '8' }, [
				{ status: 503, body: { detail: 'indisponible' } },
				ok([LE_PIN_35])
			]),
			ANNUAIRE_PAR_DEFAUT
		]);
		await open(page, '/communes');
		const form = searchForm(page);

		await combobox(page).fill('le pin');

		await expect(
			form.getByText('La recherche est momentanément indisponible. Réessayez dans un instant.')
		).toBeVisible();
		await expect(form.getByText(/Aucune commune trouvée/)).toHaveCount(0);

		await form.getByRole('button', { name: 'Relancer la recherche' }).click();

		await expect(listOptions(page).first()).toContainText('Le Pin');
		await expect(form.getByRole('status')).toHaveText('1 commune proposée');
	});
});

test.describe('Sélection de comparaison et compteur de l’en-tête', () => {
	test('ajout confirmé sur place ; le compteur passe de rien à 1 puis 2', async ({
		page,
		request
	}) => {
		await configure(request, [search({ q: 'le pin', limit: '20' }, [ok([LE_PIN_79, LE_PIN_35])])]);
		await open(page, '/communes?q=le+pin');

		await expect(comparerLink(page)).toHaveText('Comparer');
		await expect(page.locator('.site-nav-count')).toHaveCount(0);

		const outside = card(page, '79211');
		await outside.getByRole('button', { name: 'Ajouter à la comparaison' }).click();
		await expect(outside.getByRole('status')).toHaveText(
			'Ajoutée à la comparaison (1/4) · Ouvrir le comparateur'
		);
		await expect(page.locator('.site-nav-count')).toHaveText('1');
		await expect(comparerLink(page)).toHaveAccessibleName('Comparer (1 commune sélectionnée)');

		const hosted = card(page, '35999');
		await hosted.getByRole('button', { name: 'Ajouter à la comparaison' }).click();
		await expect(hosted.getByRole('status')).toHaveText(
			'Ajoutée à la comparaison (2/4) · Ouvrir le comparateur'
		);
		await expect(page.locator('.site-nav-count')).toHaveText('2');
		await expect(hosted.getByRole('link', { name: 'Ouvrir le comparateur' })).toHaveAttribute(
			'href',
			'/comparer?communes=79211,35999'
		);
		expect(await page.evaluate(() => sessionStorage.getItem('dc:comparaison'))).toBe(
			'["79211","35999"]'
		);
	});

	test('une cinquième commune est refusée avec le texte du contrat', async ({ page, request }) => {
		await page.addInitScript(() => {
			sessionStorage.setItem('dc:comparaison', JSON.stringify(['01001', '06088', '13055', '31555']));
		});
		await configure(request, [search({ q: 'le pin', limit: '20' }, [ok([LE_PIN_79, LE_PIN_35])])]);
		await open(page, '/communes?q=le+pin');

		await expect(page.locator('.site-nav-count')).toHaveText('4');
		const outside = card(page, '79211');
		await outside.getByRole('button', { name: 'Ajouter à la comparaison' }).click();

		await expect(outside.getByRole('status')).toHaveText(
			'Quatre communes au maximum : retirez-en une pour en ajouter une autre.'
		);
		await expect(page.locator('.site-nav-count')).toHaveText('4');
		expect(await page.evaluate(() => sessionStorage.getItem('dc:comparaison'))).toBe(
			'["01001","06088","13055","31555"]'
		);
	});
});

test.describe('375 px et accessibilité', () => {
	test.use({ viewport: { width: 375, height: 800 } });

	test('cartes empilées, aucun débordement, listbox à la largeur du champ, axe sans violation', async ({
		page,
		request
	}) => {
		await configure(request, [
			search({ q: 'le pin', limit: '20' }, [ok([LE_PIN_79, LE_PIN_35])]),
			search({ q: 'le pin', limit: '8' }, [ok([LE_PIN_79, LE_PIN_35])])
		]);
		await open(page, '/communes?q=le+pin');

		const outside = card(page, '79211');
		// 04.8-07 (UI-SPEC § 6.5): the row link stands for the body of the card, « Ajouter à la
		// comparaison » for its actions; the actions stay under the body.
		await expect(outside.getByRole('button', { name: /Ajouter à la comparaison/ })).toBeVisible();
		const body = await outside.locator('a.resultat-lien').boundingBox();
		const actions = await outside.locator('.ajout').boundingBox();
		expect(body && actions, 'corps et actions mesurés').toBeTruthy();
		expect(actions!.y).toBeGreaterThanOrEqual(body!.y + body!.height - 1);
		const overflow = await page.evaluate(() => ({
			scroll: document.documentElement.scrollWidth,
			inner: window.innerWidth
		}));
		expect(overflow.scroll).toBeLessThanOrEqual(overflow.inner);

		const cards = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(cards.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);

		const input = combobox(page);
		await input.fill('le pin');
		const listbox = page.getByRole('listbox', { name: 'Lieux proposés' });
		const popup = listbox.locator('..');
		await expect(listOptions(page)).toHaveCount(3);
		const field = await input.boundingBox();
		const outer = await popup.boundingBox();
		const list = await listbox.boundingBox();
		expect(field && outer && list, 'champ, popup et liste mesurés').toBeTruthy();
		// The border belongs to the outer popup; the list remains inside that aligned box.
		expect(Math.abs(outer!.width - field!.width)).toBeLessThanOrEqual(1);
		expect(Math.abs(outer!.x - field!.x)).toBeLessThanOrEqual(1);
		expect(list!.x).toBeGreaterThanOrEqual(outer!.x);
		expect(list!.x + list!.width).toBeLessThanOrEqual(outer!.x + outer!.width);
		const overflowOpen = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(overflowOpen).toBeLessThanOrEqual(375);

		const opened = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(opened.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
	});
});
