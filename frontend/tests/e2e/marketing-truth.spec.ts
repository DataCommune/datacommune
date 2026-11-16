import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
	test,
	expect,
	type APIRequestContext,
	type Locator,
	type Page,
	type TestInfo
} from '@playwright/test';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { verifierPlancherTypo } from './fixtures/quality';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Public truth guard of the home page (phase 45 « vérité publique », adapted in 03-05 to the
// direct commune search of D-38 — adapted, not removed).
//
// What this spec locks on `/`:
//   - no forbidden text of the UI-SPEC « Contrat de rédaction » (former landing hooks, personas,
//     counters not served by the API, normative verdicts, English state keys, « logements
//     classés F/G ») in the DOM (hidden text included) nor in the title and <meta> contents;
//   - no counter at all: no number of sources, datasets, departements, themes or indicators is
//     written on the page (every figure of the site comes from this instance's API, D-38);
//   - no public link targets /bien/ (since 04-08, D-101, the route only answers a 308 redirect);
//   - the prerendered page makes zero /v1 request at load time, zero page error and zero
//     console error; typing then reaches this instance's API only, on `/v1/communes/search`
//     (T-03-05-01: no third-party request, no other endpoint).
//
// The former tests on the certified counters, the certification date and the absences of the
// certified demo case left with their object (landing sections and public snapshot, D-38).
//
// API-independent at load time: /auth/me is route-mocked (anonymous session); the one search
// request is answered by the synthetic source under an exact expectation.

const FORBIDDEN_CLAIMS: ReadonlyArray<{ name: string; pattern: RegExp }> = [
	// Former landing hooks (UI-SPEC « Textes interdits »).
	{ name: 'accroche « Chaque dossier, une seule fiche »', pattern: /Chaque dossier/i },
	{ name: 'promesse « en 30 secondes / 30 sec »', pattern: /30\s*(secondes|sec\b)/i },
	{ name: 'compteur non sourcé « 20+ »', pattern: /20\s*\+/ },
	{ name: 'auto-certification « certifié »', pattern: /certifi(é|ée|és|ées|cation)/i },
	{ name: 'appel « Créer un compte gratuit »', pattern: /compte\s+gratuit/i },
	{ name: 'métrique nationale « 575 M »', pattern: /575/ },
	// Personas and promises the product does not prove.
	{ name: 'persona « agents immobiliers »', pattern: /agents?\s+immobiliers?/i },
	{ name: 'persona « diagnostiqueurs »', pattern: /diagnostiqueur/i },
	{ name: 'persona « notaires »', pattern: /\bnotaires?\b/i },
	{ name: 'partage client (« votre client »)', pattern: /votre\s+client/i },
	// 04-08 — persona and mandate texts of the former Fiche Bien (UI-SPEC « Textes interdits
	// ajoutés »): the product prepares no estimation appointment and no mandate file.
	{ name: '« Fiche Bien »', pattern: /Fiche\s+Bien/i },
	{ name: '« Exporter en PDF (dossier de mandat) »', pattern: /dossier\s+de\s+mandat/i },
	{ name: '« Prêt pour le rendez-vous d’estimation ? »', pattern: /rendez-vous\s+d['’]estimation/i },
	{ name: '« L’essentiel en 10 s »', pattern: /essentiel\s+en\s+10\s*s/i },
	{ name: 'export PDF inexistant', pattern: /\bPDF\b/i },
	{ name: 'données « anonymisées » sans preuve', pattern: /anonymis/i },
	{ name: 'offre professionnelle (gel ADR-0002)', pattern: /formules?\s+professionnelles?|offre\s+pro\b/i },
	// Infrastructure jargon does not belong on the public home or its footer.
	{ name: 'jargon « instance »', pattern: /\binstance\b/i },
	// Normative verdicts (case-sensitive: the words as a verdict label).
	{ name: 'verdict normatif', pattern: /\bFavorable\b|\bConforme\b|\bVigilance\b/ },
	// Machine-facing state keys.
	{
		name: 'clé d’état technique',
		pattern:
			/out_of_coverage|not_reported|temporary_error|not_applicable|hors_couverture|territoire_hors_perimetre/
	},
	// A DPE is a document, not a dwelling.
	{ name: '« logements classés F/G »', pattern: /logements?\s+class(é|e)s?\s+F/i }
];

// A figure followed by what the former landing counted: none may be written on `/`. 04.8-07 (D-34): the
// counts of the former examples too, DPE documents, sales and diagnostics.
const COUNTER = /\d[\d\s  ]*\s*(sources?|jeux\s+de\s+données|datasets?|départements?|thèmes|indicateurs?|communes?|documents?\s+DPE|ventes?|diagnostics?)\b/i;

test('COUNTER refuse les comptes des anciens exemples (D-34)', () => {
	for (const texte of ['5 documents DPE importés · 3 ventes sur la parcelle (2021–2025)', '5 documents DPE', '3 ventes', '12 diagnostics']) {
		expect(texte.match(COUNTER)?.[0] ?? null, texte).not.toBeNull();
	}
	// Non-vacuity of the other side: a date or a year range alone is no count.
	for (const texte of ['Relevés le 29 octobre 2026', '2021–2025']) expect(texte.match(COUNTER), texte).toBeNull();
});
const ACCOUNTS_ENABLED = (process.env.VITE_ACCOUNTS_ENABLED ?? '').trim().toLowerCase() !== 'false';

// The selected identity applies to public pages; the account form keeps its own theme.
for (const [slug, title] of [
	['couverture', 'Couverture et limites'],
	['developpeurs', 'Développeurs'],
	['mentions-legales', 'Mentions légales'],
	['confidentialite', 'Politique de confidentialité'],
	['cgu', "Conditions Générales d'Utilisation"],
	['ce-que-nous-ne-montrons-pas', 'Ce que nous ne montrons pas, et pourquoi'],
	['page-introuvable-revue-ui', 'Page introuvable']
] as const) {
	test(`B Sauge — ${slug} : identité publique, reflow, titres et axe`, async ({ page }, testInfo) => {
		await mockAuthAnonymous(page);
		for (const width of [1440, 375, 320]) {
			await page.setViewportSize({ width, height: 900 });
			await page.goto(`/${slug}`);
			const heading = page.getByRole('heading', { level: 1 });
			await expect(heading).toHaveText(title, { ignoreCase: true });
			await expect(page.locator('.public-site')).toHaveAttribute('data-ui', 'registre-sauge');
			await page.evaluate(async () => { await document.fonts.ready; window.scrollTo({ top: 0, behavior: 'instant' }); });
			expect(await heading.evaluate((el) => getComputedStyle(el).fontFamily)).toContain('Newsreader');
			// 04.8-01 (D-03): no visible text under 14 px.
			expect(await verifierPlancherTypo(page, '.public-site'), `plancher de 14 px à ${width}`).toEqual([]);
			const layout = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
			expect(layout.scroll, `no global overflow at ${width}`).toBeLessThanOrEqual(layout.width);
			const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
			expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
			if (width !== 320) await page.screenshot({ path: testInfo.outputPath(`${slug}-${width}.png`), fullPage: true, animations: 'disabled' });
		}
	});
}

test('B Sauge — le formulaire de compte conserve le thème existant', async ({ page }) => {
	await mockAuthAnonymous(page);
	await page.goto('/login');
	if (ACCOUNTS_ENABLED) {
		await expect(page.locator('.public-site')).toHaveCount(0);
		expect(await page.getByRole('heading', { level: 1 }).evaluate((el) => getComputedStyle(el).fontFamily)).toContain('Inter');
	} else {
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page introuvable');
	}
});

const LE_PIN_35 = {
	code_insee: '35999',
	nom: 'Le Pin',
	code_postal: '35170',
	departement: '35',
	population: 750,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: { etat: 'heberge', motif: null, departements: ['35'] }
};

async function mockAuthAnonymous(page: Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({
			status: 401,
			contentType: 'application/json',
			body: JSON.stringify({ detail: 'Non authentifié' })
		})
	);
}

/** Navigates to `/` and waits for hydration (the root layout reads the session once hydrated). */
async function openHome(page: Page) {
	const hydrationProbe = '__dc_hydration__';
	const hydrated = ACCOUNTS_ENABLED
		? page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me')
		: null;
	await page.goto(ACCOUNTS_ENABLED ? '/' : `/?q=${hydrationProbe}`);
	if (hydrated) await hydrated;
	else {
		// 04.8-07: the large field of the v4 home (UI-SPEC § 3.13, § 6.6).
		const input = page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
		await expect(input).toHaveValue(hydrationProbe);
		await input.fill('');
		await page.evaluate(() => history.replaceState(history.state, '', '/'));
	}
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(
		'Une adresse, en clair'
	);
}

function evidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_HOME_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

function publicMessagesEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_PUBLIC_MESSAGES_EVIDENCE_DIR;
	if (!directory) return testInfo.outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

async function expectWholeWords(locator: Locator, label: string) {
	const splitWords = await locator.evaluate((root) => {
		const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
		const failures: string[] = [];
		for (let node = walker.nextNode(); node; node = walker.nextNode()) {
			const text = node.textContent ?? '';
			for (const match of text.matchAll(/[\p{L}\p{N}][\p{L}\p{M}\p{N}'’.-]*/gu)) {
				const range = document.createRange();
				range.setStart(node, match.index ?? 0);
				range.setEnd(node, (match.index ?? 0) + match[0].length);
				const tops = [...range.getClientRects()].map((rect) => Math.round(rect.top));
				if (new Set(tops).size > 1) failures.push(match[0]);
			}
		}
		return failures;
	});
	expect(splitWords, `${label} garde ses mots entiers`).toEqual([]);
}

async function captureBelowHeader(
	page: Page,
	block: Locator,
	width: 1440 | 375,
	testInfo: TestInfo
) {
	const fixedHeight = width === 1440 ? 900 : 800;
	await page.setViewportSize({ width, height: fixedHeight });
	const header = page.locator('header.site-header');
	const [headerBox, blockBox] = await Promise.all([header.boundingBox(), block.boundingBox()]);
	expect(headerBox && blockBox, `exemples et en-tête mesurables à ${width} px`).toBeTruthy();
	const height = Math.max(fixedHeight, Math.ceil(headerBox!.height + blockBox!.height + 32));
	await page.setViewportSize({ width, height });
	await block.evaluate((element, headerHeight) => {
		const documentTop = element.getBoundingClientRect().top + window.scrollY;
		window.scrollTo({ top: Math.max(0, documentTop - Number(headerHeight) - 16), behavior: 'instant' });
	}, headerBox!.height);
	const [positionedHeader, positionedBlock] = await Promise.all([
		header.boundingBox(),
		block.boundingBox()
	]);
	expect(positionedHeader && positionedBlock, `exemples positionnés à ${width} px`).toBeTruthy();
	expect(positionedBlock!.y).toBeGreaterThanOrEqual(
		positionedHeader!.y + positionedHeader!.height
	);
	expect(positionedBlock!.y + positionedBlock!.height).toBeLessThanOrEqual(height);
	await block.screenshot({
		path: evidencePath(testInfo, `exemples-${width}.png`),
		animations: 'disabled'
	});
	await page.setViewportSize({ width, height: fixedHeight });
}

async function configureSource(request: APIRequestContext, expectations: unknown[]) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations }
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

/** Visible + hidden text of the current DOM, scripts/styles stripped (no bundler noise). */
async function domText(page: Page): Promise<string> {
	return page.evaluate(() => {
		const clone = document.body.cloneNode(true) as HTMLElement;
		clone.querySelectorAll('script, style').forEach((el) => el.remove());
		return clone.textContent ?? '';
	});
}

/** The same text without the dated example: a count of one address there is not a count of what the site hosts. */
async function domTextHorsExemple(page: Page): Promise<string> {
	return page.evaluate(() => {
		const clone = document.body.cloneNode(true) as HTMLElement;
		clone.querySelectorAll('script, style, [data-exemple]').forEach((el) => el.remove());
		return clone.textContent ?? '';
	});
}

/** All metadata surfaces of the page: title + every <meta> content attribute. */
async function metadataText(page: Page): Promise<string> {
	return page.evaluate(() => {
		const metas = Array.from(document.querySelectorAll('head meta'))
			.map((m) => m.getAttribute('content') ?? '')
			.join('\n');
		return `${document.title}\n${metas}`;
	});
}

function findViolations(text: string): string[] {
	return FORBIDDEN_CLAIMS.filter((c) => c.pattern.test(text)).map(
		(c) => `${c.name} (motif ${c.pattern})`
	);
}

/** The address search of the single field for « le pin »: nothing in a hosted BAN. */
const ADRESSES_LE_PIN = {
	method: 'GET',
	pathname: '/v1/adresses/recherche',
	query: { q: 'le pin', limite: '8' },
	minCalls: 1,
	maxCalls: 1,
	responses: [
		{
			status: 200,
			body: {
				statut: 'ok',
				donnees: {
					ordre_types: ['commune', 'adresse'],
					resultats: [],
					hebergement: { ban: { etat: 'heberge', motif: null, departements: ['35'] } }
				}
			}
		}
	]
};

test.describe('Vérité publique de l’accueil — textes interdits et compteurs', () => {
	test('premier écran, exemples réels et couverture restent lisibles à 1 440 et 375 px', async ({
		page
	}, testInfo) => {
		await mockAuthAnonymous(page);

		for (const width of [1440, 375] as const) {
			const height = width === 1440 ? 900 : 800;
			await page.setViewportSize({ width, height });
			// The previous width left the page scrolled to its examples; reloading the same URL would
			// restore that offset after hydration, past the scrollTo below.
			if (page.url() !== 'about:blank') await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
			await openHome(page);
			await page.evaluate(async () => {
				await document.fonts.ready;
				window.scrollTo({ top: 0, behavior: 'instant' });
			});
			await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

			// 04.8-07 (UI-SPEC § 6.6, § 3.13): the v4 h1, the large field and its plain button.
			const title = page.getByRole('heading', {
				level: 1,
				name: 'Une adresse, en clair'
			});
			const input = page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
			const action = page.getByRole('search', { name: 'Rechercher un lieu' }).getByRole('button', { name: 'Rechercher', exact: true });
			await expect(title).toBeVisible();
			await expect(input).toBeVisible();
			await expect(action).toBeVisible();
			await expect(input).toHaveAttribute(
				'placeholder',
				'8 rue de Metz 31000'
			);
			// 4.9 lot 1: the example reads whole at every width, measured in the font of the field.
			const place = await input.evaluate((champ: HTMLInputElement) => {
				const style = getComputedStyle(champ);
				const contexte = document.createElement('canvas').getContext('2d')!;
				contexte.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
				const utile = champ.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
				return { texte: contexte.measureText(champ.placeholder).width, utile };
			});
			expect(place.texte, `exemple entier à ${width} px`).toBeLessThanOrEqual(place.utile);

			// No departement filter on the home page since 09/11: the results page keeps it.
			await expect(page.locator('main [name="departement"]')).toHaveCount(0);

			// 04.8-07 (UI-SPEC § 6.6, D-34): the three hand-written examples, their counts and their date of
			// observation are gone; « Pour commencer » holds generic entries, named, with no place and no count.
			const pourCommencer = page.getByRole('region', { name: 'Pour commencer' });
			const commune = pourCommencer.locator('a[href="/communes"]');
			const comparaison = pourCommencer.locator('a[href="/comparer"]');
			await expect(commune).toHaveAccessibleName(/Parcourir l’annuaire des communes/);
			await expect(pourCommencer.locator('a[href="/departements"]')).toHaveAccessibleName(/Parcourir les départements/);
			// 4.9 lot 9: the legend is linked once in the content, under the scale marks.
			await expect(pourCommencer.locator('a[href="/datasets#lire"]')).toHaveCount(0);
			const legende = page.getByRole('region', { name: 'Chaque chiffre dit jusqu’où il vaut' }).locator('a[href="/datasets#lire"]');
			await expect(legende).toHaveAccessibleName(/Comment lire une fiche/);
			await expect(comparaison).toHaveAccessibleName(/Comparer des communes/);
			expect(await pourCommencer.innerText(), 'aucun compte dans les entrées (D-34)').not.toMatch(COUNTER);
			expect(await pourCommencer.innerText(), 'aucun lieu ni chiffre dans les entrées (D-34)').not.toMatch(/\d/);
			await expect(page.getByText(/Observés le/)).toHaveCount(0);
			await expect(page.getByText(/11 rue des Lilas/i)).toHaveCount(0);
			await expect(page.getByText(/Joffre/i)).toHaveCount(0);

			const coverage = page.locator('a[href="/datasets#couverture-titre"]');
			await expect(coverage).toBeVisible();
			const examples = commune.locator('xpath=ancestor::section[1]');
			await expect(examples).toBeVisible();

			for (const [name, target] of [
				['champ', input],
				['action', action],
				['exemple commune', commune],
				['légende', legende],
				['exemple comparaison', comparaison],
				['couverture', coverage]
			] as const) {
				const box = await target.boundingBox();
				expect(box, `${name} mesurable à ${width} px`).toBeTruthy();
				expect(box!.height, `${name} haut de 44 px à ${width} px`).toBeGreaterThanOrEqual(44);
			}

			const firstScreenBottom = Math.max(
				...(await Promise.all([title, input, action].map((target) => target.boundingBox()))).map(
					(box) => box!.y + box!.height
				)
			);
			expect(firstScreenBottom, `titre, champ et action dans le premier écran à ${width} px`).toBeLessThanOrEqual(
				height
			);
			const overflow = await page.evaluate(() => ({
				scroll: document.documentElement.scrollWidth,
				inner: window.innerWidth
			}));
			expect(overflow.scroll, `aucun débordement à ${width} px`).toBeLessThanOrEqual(overflow.inner);
			await expectWholeWords(title, `titre à ${width} px`);
			await expectWholeWords(examples, `exemples à ${width} px`);

			const axe = await new AxeBuilder({ page })
				.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
				.analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual(
				[]
			);

			await page.screenshot({
				path: evidencePath(testInfo, `premier-ecran-${width}.png`),
				animations: 'disabled'
			});
			await captureBelowHeader(page, examples, width, testInfo);
		}
	});

	test('le HTML servi sans JavaScript garde le formulaire GET et les trois exemples', async ({
		request
	}) => {
		const response = await request.get(`${E2E_APP_ORIGIN}/`);
		expect(response.ok()).toBe(true);
		const html = await response.text();
		// 04.8-07 (UI-SPEC § 6.6, D-34): the v4 h1; the served HTML keeps the GET form and the generic
		// entries of « Pour commencer », none of the former counts nor their date of observation.
		expect(html).toContain('Une adresse, en clair');
		expect(html).toMatch(/<form[^>]+action="\/communes"[^>]+method="GET"/i);
		for (const href of ['/communes', '/departements', '/comparer', '/datasets#lire', '/datasets#couverture-titre']) {
			expect(html).toContain(`href="${href}"`);
		}
		expect(html).toContain('Parcourir l’annuaire des communes');
		expect(html).not.toContain('5 documents DPE importés');
		expect(html).not.toContain('Observés le');
		expect(html).not.toContain('11 rue des Lilas');
		expect(html).not.toContain('/v1/');
	});

	test('aucun texte interdit du contrat dans le DOM, liste ouverte comprise', async ({
		page,
		request
	}) => {
		await configureSource(request, [
			{
				method: 'GET',
				pathname: '/v1/communes/search',
				query: { q: 'le pin', limit: '8', inclure_arrondissements: 'true' },
				minCalls: 1,
				maxCalls: 1,
				responses: [{ status: 200, body: { statut: 'ok', donnees: [LE_PIN_35] } }]
			},
			// The single field (04-17) searches the addresses of this instance with the communes.
			ADRESSES_LE_PIN
		]);
		await mockAuthAnonymous(page);
		await openHome(page);

		const texts: string[] = [await domText(page)];
		// A claim can hide in content mounted later: the suggestion list is the only one here.
		// 04.8-07: the large field and the v4 h1 of the home (UI-SPEC § 3.13, § 6.6).
		await page.getByRole('combobox', { name: 'Adresse, commune ou code postal' }).fill('le pin');
		await expect(
			page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option')
		).toHaveCount(2);
		texts.push(await domText(page));

		const all = texts.join('\n');
		expect(all, 'DOM vide — le scan serait vacuous').toContain(
			'Une adresse, en clair'
		);
		expect(findViolations(all), 'Textes interdits trouvés dans le DOM').toEqual([]);
		await expectSourceContractMet(request);
	});

	test('title, description et og:* sans persona ni texte interdit', async ({ page }) => {
		await mockAuthAnonymous(page);
		await page.goto('/');

		const description = await page
			.locator('head meta[name="description"]')
			.getAttribute('content');
		expect(description, 'meta description absente').toBeTruthy();

		const ogDescription = await page
			.locator('head meta[property="og:description"]')
			.getAttribute('content');
		expect(ogDescription, 'og:description absente').toBeTruthy();

		const meta = await metadataText(page);
		expect(findViolations(meta), `Textes interdits dans les métadonnées :\n${meta}`).toEqual([]);
	});

	test('aucun compteur (sources, jeux, départements…) n’est écrit sur l’accueil', async ({
		page
	}) => {
		await mockAuthAnonymous(page);
		await openHome(page);

		const texte = await domTextHorsExemple(page);
		// Non-vacuity: the head of the page is read (no departement filter on the home page since 09/11).
		expect(texte).toContain('Une adresse, en clair');
		expect(texte.match(COUNTER)?.[0] ?? null, 'compteur écrit sur l’accueil').toBeNull();
		await expect(page.getByText('Ces chiffres sont figés')).toBeVisible();
		const meta = await metadataText(page);
		expect(meta.match(COUNTER)?.[0] ?? null, 'compteur dans les métadonnées').toBeNull();
	});
});

test.describe('Vérité publique de l’accueil — liens', () => {
	test('aucun lien public ne cible /bien/ (fiche bien = guard (app))', async ({ page }) => {
		await mockAuthAnonymous(page);
		await page.goto('/');
		await expect(page.locator('a[href^="/bien"], a[href*="/bien/"]')).toHaveCount(0);

		// Still none once hydrated.
		await openHome(page);
		await expect(page.locator('a[href^="/bien"], a[href*="/bien/"]')).toHaveCount(0);
	});
});

test.describe('Vérité publique de la page développeurs', () => {
	test('aucun jargon d’instance ni compteur public', async ({ page }, testInfo) => {
		await mockAuthAnonymous(page);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			const hydrated = ACCOUNTS_ENABLED
				? page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me')
				: null;
			await page.goto('/developpeurs');
			if (hydrated) await hydrated;
			else {
				await page.evaluate(
					() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
				);
			}
			const main = page.getByRole('main');
			await expect(main.getByRole('heading', { level: 1, name: 'Développeurs' })).toBeVisible();
			if (!ACCOUNTS_ENABLED) {
				await expect(main.getByText('Ce site ne propose pas de comptes.', { exact: false })).toBeVisible();
				await expect(main.getByRole('link', { name: 'Obtenir une clé API' })).toHaveCount(0);
			}
			const texte = await domText(page);
			expect(texte).not.toMatch(/\binstance\b/i);
			expect(texte.match(COUNTER)?.[0] ?? null, 'compteur écrit sur la page développeurs').toBeNull();
			await main.screenshot({
				path: publicMessagesEvidencePath(
					testInfo,
					`developpeurs-${ACCOUNTS_ENABLED ? 'comptes-actifs' : 'comptes-desactives'}-${width}.png`
				),
				animations: 'disabled'
			});
		}
	});
});

test.describe('Vérité publique de l’accueil — page statique et saine', () => {
	test('zéro requête /v1 au chargement ; à la saisie, les seules recherches de cette instance ; zéro erreur', async ({
		page,
		request
	}) => {
		await configureSource(request, [
			{
				method: 'GET',
				pathname: '/v1/communes/search',
				query: { q: 'le pin', limit: '8', inclure_arrondissements: 'true' },
				minCalls: 1,
				maxCalls: 1,
				responses: [{ status: 200, body: { statut: 'ok', donnees: [LE_PIN_35] } }]
			},
			// The single field (04-17) searches the addresses of this instance with the communes.
			ADRESSES_LE_PIN
		]);
		await installLocalNetworkGuard(page);
		await mockAuthAnonymous(page);

		const pageErrors: string[] = [];
		const consoleErrors: string[] = [];
		const apiRequests: string[] = [];
		page.on('pageerror', (err) => pageErrors.push(String(err)));
		page.on('console', (msg) => {
			// The mocked anonymous /auth/me legitimately answers 401 and Chromium logs the
			// resource failure as a console error — that is the expected anonymous session
			// check, not a page defect. Any other status (404 asset, 500…) still fails.
			if (msg.type() === 'error' && !/status of 401/.test(msg.text())) {
				consoleErrors.push(msg.text());
			}
		});
		page.on('request', (req) => {
			if (/\/v1\//.test(req.url())) apiRequests.push(req.url());
		});

		await openHome(page);
		expect(apiRequests, 'L’accueil prérendu ne doit déclencher aucune requête /v1').toEqual([]);

		// Typing: one debounced search of communes and one of addresses (the single field, 04-17),
		// to this instance's API, on the two search endpoints only (D-105: no third-party service).
		// 04.8-07: the large field of the v4 home (UI-SPEC § 3.13, § 6.6).
		await page.getByRole('combobox', { name: 'Adresse, commune ou code postal' }).pressSequentially('le pin');
		await expect(
			page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option')
		).toHaveCount(2);

		expect(apiRequests).toHaveLength(2);
		const searched = apiRequests.map((url) => new URL(url));
		for (const url of searched) {
			expect(url.origin).toBe(E2E_SOURCE_ORIGIN);
			expect(url.searchParams.get('q')).toBe('le pin');
		}
		expect(searched.map((url) => url.pathname).sort()).toEqual([
			'/v1/adresses/recherche',
			'/v1/communes/search'
		]);

		expect(pageErrors, 'pageerror pendant hydratation/saisie').toEqual([]);
		expect(consoleErrors, 'console errors pendant hydratation/saisie').toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
		await expectSourceContractMet(request);
	});
});
