import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';

// Comparator and export, wave A of 2026-11-10 (review R-14, R-21, the shape of R-06; the entry to the
// comparison of arrondissements; the « Commune » mark of an inherited value; inventory C4, C11).
// Headless runner, isolated context, every SSR read served by the local fail-closed source (AGENTS.md).
// R-01 and R-05 keep their own specs (`revue-r01-…`, `revue-r05-…`).
//
// Responses: the frozen harness response of 03-09 (`__fixtures__/commune-comparaison.json`). The variant
// with an arrondissement is SYNTHETIC: it keeps the served shape and adds what `/v1/commune` serves on an
// arrondissement (`heritages`, R-09); the page computes nothing from it.

interface Cellule { code_insee: string; etat: string | null; valeur: unknown; periode: string | null; ecart: { valeur: number; unite: string } | null; motif_non_calcul: string | null; identique_a_la_reference: boolean | null }
interface Critere { cle: string; field_id: string; unite: string; identiques: boolean; cellules: Cellule[] }
interface Commune { code_insee: string; nom: string; departement: string; hebergement: unknown; synthese: Record<string, unknown> | null }
interface Reponse { statut: string; donnees: { communes: Commune[]; criteres: Critere[]; codes_inconnus: string[] }; meta: { source: unknown[] } | null }

const REPONSES = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url), 'utf8')) as Record<string, Reponse>;
const DEUX = REPONSES.deux_communes_hebergees;
const CODES = ['44991', '44992'];
const LECTURES = 6;

type Reply = { status: number; body: unknown; headers?: Record<string, string> };
type Contract = { method: string; pathname: string; query: Record<string, string>; minCalls: number; maxCalls: number; responses: Reply[] };

const comparaison = (codes: string[], reponse: Reply): Contract => ({
	method: 'GET', pathname: '/v1/communes/comparaison', query: { codes: codes.join(',') },
	minCalls: 1, maxCalls: LECTURES, responses: Array.from({ length: LECTURES }, () => reponse)
});

async function configure(request: APIRequestContext, expectations: Contract[]) {
	expect((await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } })).ok()).toBe(true);
}
/**
 * Opens a page and waits for its hydration. C2: the categories of a comparison of communes are folded on
 * arrival; the cases that read their rows unfold them here (`replie: true` keeps the arrival state).
 */
async function open(page: Page, url: string, { replie = false }: { replie?: boolean } = {}) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	const reponse = await page.goto(url);
	await hydrated;
	if (!replie) await page.locator('details.compare-groupe-pli').evaluateAll((plis) => plis.forEach((pli) => ((pli as HTMLDetailsElement).open = true)));
	return reponse;
}
const norm = (value: string | null | undefined) => value?.replace(/\s+/g, ' ').trim() ?? null;

/** The 11e arrondissement of Paris against a commune: the ABC zone of the 11e is the one of Paris (SYNTHETIC). */
function avecArrondissement(): Reponse {
	const reponse = structuredClone(DEUX);
	const [arrondissement, commune] = reponse.donnees.communes;
	arrondissement.code_insee = '75111';
	arrondissement.nom = 'Paris 11e Arrondissement';
	arrondissement.departement = '75';
	Object.assign(arrondissement.synthese!, {
		code_insee: '75111', nom_commune: 'Paris 11e Arrondissement', code_departement: '75', zone_abc: 'Abis',
		type_zone: 'arrondissement', commune_parent: '75056',
		heritages: { zone_abc: { echelle: 'commune', code_insee: '75056', nom: 'Paris' } }
	});
	Object.assign(commune.synthese!, { zone_abc: 'B1' });
	for (const item of [arrondissement, commune]) delete (item.synthese!.etats as Record<string, unknown> | undefined)?.zone_abc;
	for (const critere of reponse.donnees.criteres) {
		critere.cellules[0].code_insee = '75111';
		if (critere.cle !== 'zone_abc') continue;
		critere.identiques = false;
		critere.cellules = [
			{ ...critere.cellules[0], etat: 'value', valeur: 'Abis' },
			{ ...critere.cellules[1], etat: 'value', valeur: 'B1', motif_non_calcul: 'non_numerique', identique_a_la_reference: false }
		];
	}
	return reponse;
}

test.describe('R-14 — `diff=1` dès le rendu serveur', () => {
	test('la case est servie cochée et les lignes identiques ne sont pas servies', async ({ request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		const html = await (await request.get(`/comparer?communes=${CODES.join(',')}&diff=1`)).text();
		const filtre = /<input[^>]*type="checkbox"[^>]*>(?=\s*<span[^>]*>Différences seulement)/.exec(html)?.[0] ?? '';
		expect(filtre, 'case « Différences seulement » du HTML servi').toMatch(/\schecked/);
		expect(html).toContain('data-identiques="false"');
		expect(html).not.toContain('data-identiques="true"');
	});

	test('témoin : sans `diff`, la case est servie décochée avec toutes les lignes', async ({ request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		const html = await (await request.get(`/comparer?communes=${CODES.join(',')}`)).text();
		const filtre = /<input[^>]*type="checkbox"[^>]*>(?=\s*<span[^>]*>Différences seulement)/.exec(html)?.[0] ?? '';
		expect(filtre).not.toBe('');
		expect(filtre).not.toMatch(/\schecked/);
		expect(html).toContain('data-identiques="true"');
	});
});

test.describe('R-21 — une comparaison en panne ne répond pas 200', () => {
	test('la source en échec : 503 avec le Retry-After servi, la page lisible, sans marqueur interne', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 503, body: { detail: 'Service indisponible' }, headers: { 'Retry-After': '45' } })]);
		const reponse = await open(page, `/comparer?communes=${CODES.join(',')}`);
		expect(reponse!.status()).toBe(503);
		expect(reponse!.headers()['retry-after']).toBe('45');
		expect(reponse!.headers()['x-dc-etat-page']).toBeUndefined();
		expect(reponse!.headers()['x-dc-retry-after']).toBeUndefined();
		await expect(page.getByRole('alert')).toContainText('momentanément indisponibles');
		await expect(page.getByRole('button', { name: 'Recharger la comparaison' })).toBeVisible();
	});

	test('une panne sans Retry-After servi : 503 et le délai par défaut', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 500, body: { detail: 'x' } })]);
		const reponse = await open(page, `/comparer?communes=${CODES.join(',')}`);
		expect(reponse!.status()).toBe(503);
		expect(reponse!.headers()['retry-after']).toBe('60');
	});

	test('une commune contre son arrondissement est un refus, pas une panne : 200, sans Retry-After', async ({ page, request }) => {
		await configure(request, [comparaison(['75056', '75112'], { status: 422, body: { detail: 'x' } })]);
		const reponse = await open(page, '/comparer?communes=75056,75112');
		expect(reponse!.status()).toBe(200);
		expect(reponse!.headers()['retry-after']).toBeUndefined();
		await expect(page.getByRole('alert')).toContainText('Une commune et son arrondissement ne se comparent pas');
	});

	test('témoin : une comparaison servie répond 200', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		expect((await open(page, `/comparer?communes=${CODES.join(',')}`))!.status()).toBe(200);
	});
});

test.describe('forme de R-06 — un repli ouvert avant l’hydratation reste ouvert', () => {
	for (const [nom, selecteur] of [
		['« Référence des écarts »', 'details.comparer-lecture'],
		['« Modifier la sélection »', 'details.comparer-modifier']
	] as const) {
		test(`${nom} ouvert pendant le chargement des scripts`, async ({ page, request }) => {
			await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
			await page.setViewportSize({ width: 1440, height: 900 });
			let liberer!: () => void;
			const libere = new Promise<void>((resolve) => (liberer = resolve));
			// The scripts wait: the server render is all there is when the reader clicks.
			await page.route(/\.(js|ts|svelte|mjs)(\?|$)|\/@fs\/|\/@vite\/|\/\.svelte-kit\//, async (route) => {
				await libere;
				await route.fallback();
			});
			const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me', { timeout: 30_000 });
			await page.goto(`/comparer?communes=${CODES.join(',')}`, { waitUntil: 'commit' });
			const pli = page.locator(selecteur);
			await pli.locator(':scope > summary').click();
			await expect(pli).toHaveJSProperty('open', true);
			liberer();
			await hydratee;
			await expect(pli, 'closed by the hydration of bind:open').toHaveJSProperty('open', true);
		});
	}
});

test.describe('valeur héritée de la commune (R-09) — repère « Commune » dans le comparateur', () => {
	test('la cellule héritée porte le repère, la page dit ce qu’il veut dire, le volet nomme la commune', async ({ page, request }) => {
		await configure(request, [comparaison(['75111', '44992'], { status: 200, body: avecArrondissement() })]);
		await open(page, '/comparer?communes=75111,44992');
		const heritee = page.locator('tr[data-cle="zone_abc"] td[data-code="75111"]');
		await expect(heritee.locator('.state-value')).toHaveText('Abis');
		await expect(heritee.locator('.compare-echelle-ecran')).toHaveText('Commune');
		await expect(heritee.locator('.compare-echelle-clair')).toBeHidden();
		await expect(page.locator('tr[data-cle="zone_abc"] td[data-code="44992"] .compare-echelle')).toHaveCount(0);
		await expect(page.locator('.compare-echelle')).toHaveCount(1);
		expect(norm(await page.locator('.cmp-note-echelle').textContent())).toBe(
			'Les valeurs marquées « Commune » valent pour tout Paris, pas pour le seul arrondissement.'
		);
		// On paper the mark gives way to its object in words.
		await page.emulateMedia({ media: 'print' });
		await expect(heritee.locator('.compare-echelle-clair')).toHaveText('Commune de Paris');
		await expect(heritee.locator('.compare-echelle-ecran')).toBeHidden();
	});

	test('témoin : deux communes ordinaires, aucun repère ni phrase', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		await expect(page.locator('.compare-echelle')).toHaveCount(0);
		await expect(page.locator('.cmp-note-echelle')).toHaveCount(0);
	});
});

test.describe('entrée vers la comparaison d’arrondissements', () => {
	const paris = (code: string, extra: Record<string, unknown> = {}) => ({
		method: 'GET', pathname: '/v1/commune', query: { code_insee: code }, minCalls: 1, maxCalls: LECTURES,
		responses: Array.from({ length: LECTURES }, () => ({
			status: 200,
			body: {
				statut: 'ok',
				donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC, code_insee: code, code_departement: '75', ...extra }],
				meta: { source: [] }
			}
		}))
	});
	const pli = (page: Page) => page.locator('.fiche-header details').filter({ hasText: 'Comparer des arrondissements de Paris' });

	test('fiche de Paris : deux arrondissements choisis ouvrent leur comparaison', async ({ page, request }) => {
		await configure(request, [
			paris('75056', { nom_commune: 'Paris' }),
			{ ...comparaison(['75103', '75111'], { status: 503, body: { detail: 'x' } }), minCalls: 0 }
		]);
		await open(page, '/commune/75056');
		await expect(pli(page)).not.toHaveAttribute('open', '');
		await pli(page).locator('summary').click();
		await expect(page.getByRole('checkbox', { name: /arrondissement$/ })).toHaveCount(20);
		const comparer = pli(page).getByRole('button', { name: 'Comparer' });
		await expect(comparer).toBeDisabled();
		await page.getByRole('checkbox', { name: '11e arrondissement', exact: true }).check();
		await page.getByRole('checkbox', { name: '3e arrondissement', exact: true }).check();
		await expect(comparer).toBeEnabled();
		// 44 px targets, inside the window at 375 px.
		const cible = await page.getByRole('checkbox', { name: '11e arrondissement', exact: true }).locator('xpath=..').boundingBox();
		expect(cible!.height).toBeGreaterThanOrEqual(44);
		await comparer.click();
		await expect(page).toHaveURL(/\/comparer\?communes=75103,75111$/);
	});

	test('fiche d’un arrondissement : le sien est coché et passe en première colonne ; quatre au plus', async ({ page, request }) => {
		await configure(request, [
			paris('75111', { nom_commune: 'Paris 11e Arrondissement', type_zone: 'arrondissement', commune_parent: '75056' }),
			{ ...comparaison(['75111', '75101'], { status: 503, body: { detail: 'x' } }), minCalls: 0 }
		]);
		await page.setViewportSize({ width: 375, height: 812 });
		await open(page, '/commune/75111');
		await pli(page).locator('summary').click();
		await expect(page.getByRole('checkbox', { name: '11e arrondissement', exact: true })).toBeChecked();
		for (const rang of ['1er', '2e', '3e']) await page.getByRole('checkbox', { name: `${rang} arrondissement`, exact: true }).check();
		await expect(page.getByRole('checkbox', { name: '4e arrondissement', exact: true })).toBeDisabled();
		await page.getByRole('checkbox', { name: '2e arrondissement', exact: true }).uncheck();
		await page.getByRole('checkbox', { name: '3e arrondissement', exact: true }).uncheck();
		await expect(page.getByRole('checkbox', { name: '4e arrondissement', exact: true })).toBeEnabled();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no horizontal scroll at 375 px').toBe(true);
		await pli(page).getByRole('button', { name: 'Comparer' }).click();
		await expect(page).toHaveURL(/\/comparer\?communes=75111,75101$/);
	});

	test('sans JavaScript : le formulaire envoie les cases cochées, que le comparateur lit', async ({ browser, request }) => {
		await configure(request, [
			paris('75056', { nom_commune: 'Paris' }),
			{ ...comparaison(['75103', '75111'], { status: 503, body: { detail: 'x' } }), minCalls: 0 }
		]);
		const contexte = await browser.newContext({ javaScriptEnabled: false });
		const page = await contexte.newPage();
		try {
			await page.goto('/commune/75056');
			await pli(page).locator('summary').click();
			await page.getByRole('checkbox', { name: '3e arrondissement', exact: true }).check();
			await page.getByRole('checkbox', { name: '11e arrondissement', exact: true }).check();
			await pli(page).getByRole('button', { name: 'Comparer' }).click();
			await expect(page).toHaveURL(/\/comparer\?communes=75103&communes=75111$/);
			// The comparator read both codes: its one comparison call names them.
			const lues = (await (await request.get(`${E2E_SOURCE_ORIGIN}/__requests`)).json()) as { requests: { pathname: string; query: Record<string, string> }[] };
			expect(lues.requests.filter((row) => row.pathname === '/v1/communes/comparaison').map((row) => row.query.codes)).toEqual(['75103,75111']);
		} finally {
			await contexte.close();
		}
	});
});

test.describe('inventaire C4, C11', () => {
	test('C4 : l’écart se lit seul, sa référence une fois dans le coin du tableau', async ({ page, request }) => {
		const reponse = structuredClone(DEUX);
		const revenu = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		revenu.identiques = false;
		revenu.cellules[1] = { ...revenu.cellules[1], ecart: { valeur: 999, unite: 'euro_per_uc_year' }, motif_non_calcul: null, identique_a_la_reference: false };
		await configure(request, [comparaison(CODES, { status: 200, body: reponse })]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const ecart = page.locator('tr[data-cle="revenu_median"] td[data-code="44992"] .compare-ecart');
		// What a screen reader hears, then what the eye reads.
		expect(norm(await ecart.textContent())).toBe('+999 €/UC/an par rapport à Ancienne');
		const cache = await ecart.locator('.sr-only').boundingBox();
		expect(cache!.width).toBeLessThanOrEqual(1);
		const coin = page.locator('thead th.compare-coin .compare-reference-ecarts');
		await expect(coin).toBeVisible();
		await expect(coin).toHaveText('Écarts par rapport à Ancienne');
		await expect(page.getByRole('columnheader', { name: 'Critère', exact: true })).toHaveCount(1);
	});

	test('C11 : la bascule « Totaux annuels » est une cible de 44 px, et aucun bouton de détail ne porte de tiret', async ({ page, request }) => {
		const documents = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/dpe-comparaison.json', import.meta.url), 'utf8')) as Record<
			string,
			{ donnees: { colonnes: { numero: string }[] } }
		>;
		const trois = documents.meme_methode_trois;
		const numeros = trois.donnees.colonnes.map((colonne) => colonne.numero);
		await configure(request, [
			{
				method: 'GET', pathname: '/v1/dpe/comparaison', query: { numeros: numeros.join(',') }, minCalls: 1, maxCalls: LECTURES,
				responses: Array.from({ length: LECTURES }, () => ({ status: 200, body: trois }))
			}
		]);
		for (const largeur of [1440, 375]) {
			await page.setViewportSize({ width: largeur, height: 900 });
			await open(page, `/comparer?dpe=${numeros.join(',')}`);
			const resume = page.locator('details.compare-detail > summary');
			await expect(resume).toHaveText('Totaux annuels');
			expect((await resume.boundingBox())!.height, `hauteur de la bascule à ${largeur} px`).toBeGreaterThanOrEqual(44);
			const noms = await page.locator('button.source-button').evaluateAll((boutons) => boutons.map((bouton) => bouton.getAttribute('aria-label') ?? ''));
			expect(noms.length).toBeGreaterThan(0);
			expect(noms.filter((nom) => nom.includes('—'))).toEqual([]);
		}
	});

	test('C4 : à 320 et 375 px, le texte gardé pour les lecteurs d’écran n’élargit pas la page', async ({ page, request }) => {
		for (const largeur of [320, 375]) {
			await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
			await page.setViewportSize({ width: largeur, height: 812 });
			await open(page, `/comparer?communes=${CODES.join(',')}`);
			expect(await page.evaluate(() => document.documentElement.scrollWidth), `largeur du document à ${largeur} px`).toBeLessThanOrEqual(largeur);
		}
	});
});

// Lot 2 (arbitrated by Ianis on 2026-11-10): C1, the three categories of the commune fiche; C3, the period and
// the source behind the information button; C5, the rents among the landmarks; C9, a row header that no
// longer takes ten lines at 375 px.
test.describe('lot 2 — C1, C3, C5, C9', () => {
	test('C1 et C5 : « Repères » avec les loyers, puis les trois catégories de la fiche commune', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const groupes = page.locator('tbody[data-groupe]');
		expect(await groupes.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-groupe')))).toEqual(['reperes', 'q-prix', 'q-logement', 'q-quotidien']);
		expect(await groupes.locator('th[scope="rowgroup"]').evaluateAll((nodes) => nodes.map((node) => node.textContent?.replace(/\s+/g, ' ').trim()))).toEqual([
			'Repères',
			'Prix et ventes',
			'Logement et DPE',
			'Vivre dans la commune'
		]);
		const reperes = await page.locator('tbody[data-groupe="reperes"] tr[data-cle]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-cle')));
		expect(reperes).toEqual(expect.arrayContaining(['loyer_m2_appartement', 'loyer_m2_maison']));
		await expect(page.locator('tbody[data-groupe="q-prix"] tr[data-cle^="loyer_m2_"]')).toHaveCount(0);
		// Each served criterion is still shown once.
		const cles = await page.locator('tr[data-cle]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-cle')));
		expect(new Set(cles).size).toBe(cles.length);
		expect(cles).toHaveLength(62);
	});

	test('C3 et C9 : ni période ni source sur la face ; à 375 px un libellé ne tient plus sur dix lignes', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		await page.setViewportSize({ width: 375, height: 812 });
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		await expect(page.locator('.compare-meta-commune, .compare-meta')).toHaveCount(0);
		await expect(page.locator('table.compare-table')).not.toContainText(/Source\s:/);
		const entetes = await page.locator('th.compare-critere').evaluateAll((cellules) =>
			cellules.map((cellule) => {
				const style = getComputedStyle(cellule);
				const texte = cellule.getBoundingClientRect().height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
				return { cle: cellule.closest('tr')?.getAttribute('data-cle') ?? '', lignes: Math.round(texte / parseFloat(style.lineHeight)) };
			})
		);
		const plusHaut = entetes.reduce((a, b) => (b.lignes > a.lignes ? b : a));
		console.log(`RELEVE c9 375px ${JSON.stringify({ entetes: entetes.length, plus_haut: plusHaut, moyenne: Math.round((entetes.reduce((n, e) => n + e.lignes, 0) / entetes.length) * 10) / 10 })}`);
		expect(plusHaut.lignes, `${plusHaut.cle} : lignes de l'en-tête le plus haut`).toBeLessThan(10);
	});
});

// Lot 3: C2, the categories of a comparison of communes fold; C10, they wear the tint of their category;
// C8, the empty comparator offers one short example without growing taller than the 1440 × 900 window.
test.describe('lot 3 — C2, C8, C10', () => {
	const lignes = (page: Page, groupe: string) => page.locator(`tbody[data-groupe="${groupe}"] > tr[data-cle]`);
	const pli = (page: Page, groupe: string) => page.locator(`tbody[data-groupe="${groupe}"] details.compare-groupe-pli`);

	for (const [largeur, hauteur] of [[1440, 900], [375, 812]] as const) {
		test(`C2 à ${largeur} px : « Repères » ouvert, les catégories repliées à l’arrivée, ouvertes par leur titre`, async ({ page, request }) => {
			await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
			await page.setViewportSize({ width: largeur, height: hauteur });
			await open(page, `/comparer?communes=${CODES.join(',')}`, { replie: true });
			await expect(pli(page, 'reperes')).toHaveCount(0);
			await expect(lignes(page, 'reperes').first()).toBeVisible();
			// UI-012: a served value of the first group stays in the first screen.
			const premiere = await page.locator('tbody[data-groupe="reperes"] td .state-value, tbody[data-groupe="reperes"] td .state-badge').first().boundingBox();
			console.log(`RELEVE c2 ${largeur}px premiere_valeur_bas=${Math.round(premiere!.y + premiere!.height)} fenetre=${hauteur} page=${await page.evaluate(() => document.documentElement.scrollHeight)}`);
			expect(premiere!.y + premiere!.height, 'bas de la première valeur').toBeLessThanOrEqual(hauteur);
			for (const groupe of ['q-prix', 'q-logement', 'q-quotidien']) {
				await expect(pli(page, groupe)).not.toHaveAttribute('open', '');
				await expect(lignes(page, groupe).first()).toBeHidden();
			}
			const resume = pli(page, 'q-logement').locator('summary');
			await expect(resume).toHaveText('Logement et DPE');
			expect((await resume.boundingBox())!.height).toBeGreaterThanOrEqual(44);
			await resume.focus();
			await page.keyboard.press('Enter');
			await expect(pli(page, 'q-logement')).toHaveAttribute('open', '');
			await expect(lignes(page, 'q-logement').first()).toBeVisible();
			await expect(lignes(page, 'q-prix').first()).toBeHidden();
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'aucun défilement horizontal').toBe(true);
			// On paper every row is printed, whatever the state of its fold.
			await page.emulateMedia({ media: 'print' });
			for (const groupe of ['q-prix', 'q-quotidien']) await expect(lignes(page, groupe).first()).toBeVisible();
		});
	}

	test('C2 : « Accès direct » ouvre la catégorie qu’il vise ; sans JavaScript le titre l’ouvre encore', async ({ page, request, browser }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		await open(page, `/comparer?communes=${CODES.join(',')}`, { replie: true });
		await page.locator('details.comparer-lecture > summary').click();
		await page.getByRole('navigation', { name: 'Accès aux groupes de comparaison' }).getByRole('link', { name: 'Vivre dans la commune' }).click();
		await expect(pli(page, 'q-quotidien')).toHaveAttribute('open', '');
		await expect(page.locator('#comparaison-groupe-q-quotidien')).toBeFocused();
		await expect(lignes(page, 'q-quotidien').first()).toBeVisible();

		const contexte = await browser.newContext({ javaScriptEnabled: false });
		const sansScript = await contexte.newPage();
		try {
			await sansScript.goto(`/comparer?communes=${CODES.join(',')}`);
			await expect(lignes(sansScript, 'q-prix').first()).toBeHidden();
			await pli(sansScript, 'q-prix').locator('summary').click();
			await expect(lignes(sansScript, 'q-prix').first()).toBeVisible();
		} finally {
			await contexte.close();
		}
	});

	test('C10 : chaque catégorie porte la teinte de sa catégorie de fiche, « Repères » reste neutre', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, { status: 200, body: DEUX })]);
		await open(page, `/comparer?communes=${CODES.join(',')}`, { replie: true });
		const fonds = await page.locator('tbody[data-groupe] > tr.compare-groupe > th').evaluateAll((titres) => titres.map((titre) => getComputedStyle(titre).backgroundColor));
		expect(new Set(fonds).size, `fonds des titres : ${fonds.join(' | ')}`).toBe(4);
		for (const groupe of ['q-prix', 'q-logement', 'q-quotidien']) await expect(pli(page, groupe).locator('summary svg.icone-categorie')).toBeVisible();
		await expect(page.locator('tbody[data-groupe="reperes"] > tr.compare-groupe svg')).toHaveCount(0);
	});

	// DECLARED CHANGE (direction A of 2026-11-10, decisions D3 and D6): the single example « Lyon et Bordeaux »,
	// never validated, gives way to the ready comparisons of `comparateur-textes.ts`, and the empty comparator,
	// which now previews the shape of a result, is no longer held under 900 px (`forme-publique.spec.ts`
	// measures another short page). Geometry of the new state: `comparateur-lisibilite.spec.ts`.
	test('C8 : des comparaisons toutes prêtes sous le bouton d’ajout, sans commune à arrondissements', async ({ page, request }) => {
		await configure(request, []);
		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, '/comparer');
		const exemples = page.locator('.comparer-exemples a');
		await expect(exemples.first()).toBeVisible();
		const liens = await exemples.evaluateAll((items) => items.map((item) => item.getAttribute('href') ?? ''));
		expect(liens.length).toBeGreaterThanOrEqual(2);
		for (const lien of liens) {
			expect(lien).toMatch(/^\/comparer\?communes=\d{5}(,\d{5}){1,3}$/);
			expect(lien, 'Paris, Lyon et Marseille entiers servent « Non renseigné » sur les loyers').not.toMatch(/75056|69123|13055/);
		}
	});
});
