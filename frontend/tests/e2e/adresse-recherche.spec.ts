import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, chromium, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, enRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Plan 04-17 (LIEU-01, SEM-01, UI-01; D-105..D-107, D-112) — the single search field and the
// address page.
//
// What this spec locks:
//   - tracer: « 12 rue des Lilas » typed on `/`, the option typed « Adresse » chosen with the
//     keyboard, `/adresse/{ban_id}` opens with the served address as h1, the overline « Adresse »,
//     `noindex, nofollow` in the head and in `x-robots-tag`; no coordinate in the URL nor in the
//     served HTML (the synthetic API slips one in: the server projection must drop it);
//   - every request reaches this instance's API only, under exact expectations of the synthetic
//     source (browser searches and SSR alike): no third-party address service (D-105);
//   - the single field: served order (5 / 3), agreed announcements, street without number (no
//     navigation, caret at the start), addresses out of coverage under the field with the served
//     territory, partial outage of either half (a 429 stays typed); `/communes?q=` typed groups in
//     the served order and the « Aucun résultat » contract; at 375, 768 and 1 280 px the listbox
//     takes the width of the field, nothing overflows and a long street wraps without ellipsis;
//   - the address page: out of coverage (200, warn notice, two links, no marker), unknown key
//     (404), street opened by a link (404 « Précisez le numéro »), outage (503, one notice, no
//     value, « Recharger la fiche » reads the address again), 409 and 429 typed by `panne.ts`; the
//     longest label of the fixture wraps at 320 px and at a real 200 % browser zoom.

type Reply = { status: number; body: unknown };
type Expectation = {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

const COORDONNEES = /-?\d{1,3}\.\d{3,},-?\d{1,3}\.\d{3,}/;
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
const MESSAGE_NUMERO_PROCHE =
	"Le numéro 12 n'apparaît pas dans ces résultats. Voici les résultats proches renvoyés.";
const ACCOUNTS_ENABLED = (process.env.VITE_ACCOUNTS_ENABLED ?? '').trim().toLowerCase() !== 'false';

const HEBERGE = { etat: 'heberge', motif: null, departements: ['85'] };
const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };

const LILAS = {
	type_resultat: 'adresse',
	ban_id: '85191_0420_00012',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	code_commune: '85191',
	nom_commune: 'La Roche-sur-Yon',
	departement: { code: '85', nom: 'Vendée' }
};

const IDENTITE_LILAS = {
	etat: 'etablie',
	ban_id: '85191_0420_00012',
	libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null },
	// Not in the 04-02 contract: a coordinate the server projection must never serialize.
	position: '-1.426123,46.670345'
};

const okCommunes = (donnees: unknown[]): Reply => ({ status: 200, body: { statut: 'ok', donnees } });

function okAdresses(
	resultats: unknown[],
	ordre: string[],
	ban: unknown = HEBERGE,
	correspondanceNumero?: unknown
): Reply {
	return {
		status: 200,
		body: {
			statut: 'ok',
			donnees: {
				ordre_types: ordre,
				resultats,
				hebergement: { ban },
				...(correspondanceNumero === undefined ? {} : { correspondance_numero: correspondanceNumero })
			},
			meta: { source: SOURCE_BAN }
		}
	};
}

function okFiche(identite: unknown, couverture: unknown = { etat: 'heberge', motif: null }): Reply {
	return {
		status: 200,
		body: { statut: 'ok', donnees: { identite, couverture }, meta: { source: [SOURCE_BAN] } }
	};
}

function attente(pathname: string, query: Record<string, string>, replies: Reply[], minCalls = replies.length): Expectation {
	return { method: 'GET', pathname, query, minCalls, maxCalls: replies.length, responses: replies };
}

const communes = (query: Record<string, string>, replies: Reply[]) =>
	attente('/v1/communes/search', { ...query, inclure_arrondissements: 'true' }, replies);
const adresses = (query: Record<string, string>, replies: Reply[]) => attente('/v1/adresses/recherche', query, replies);
const fiche = (banId: string, replies: Reply[], minCalls = replies.length) =>
	attente(cheminRapport(banId), REQUETE_RAPPORT, replies.map(enRapport), minCalls);

// 04-18: the address page reads the first page of its declared DPE documents in parallel with its
// identity. Each identity expectation gets its companion `/dpe?page=1` read, with as many replies
// (an address without document); this spec does not assert the documents (adresse-fiche.spec.ts
// does), and the exact contract keeps refusing any other request.
const SANS_DOCUMENT: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			etat: 'value', motif: null, total: 0, page: 1, pages: 1, par_page: 10, generation: '0123456789abcdef',
			distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 }, date_import: '2026-09-30', documents: [],
			selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] },
			signal_plusieurs_logements: null,
			source: { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' }
		}
	}
};

function avecDocuments(expectations: Expectation[]): Expectation[] {
	return expectations.flatMap((attendue) =>
		/^\/v1\/adresse\/[^/]+\/rapport$/.test(attendue.pathname)
			? [attendue, attente(attendue.pathname.replace(/\/rapport$/, '/dpe'), { page: '1' }, attendue.responses.map(() => SANS_DOCUMENT), 0)]
			: [attendue]
	);
}

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: avecVentesDeLaParcelle(avecDocuments(expectations)) }
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

async function countApiRequests(request: APIRequestContext, pathname: string): Promise<number> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { requests: { pathname: string }[] };
	return audit.requests.filter((row) => row.pathname === pathname).length;
}

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function open(page: Page, url: string) {
	const hydrated = ACCOUNTS_ENABLED
		? page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me')
		: null;
	await page.goto(url);
	if (hydrated) await hydrated;
}

// 04.8-07: `/` and `/communes` carry the large field (UI-SPEC § 3.13, § 6.5, § 6.6), labelled
// « Adresse, commune ou code postal », its plain button « Rechercher ».
function champ(page: Page) {
	return page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
}

function boutonRechercher(page: Page) {
	return page.getByRole('search', { name: 'Rechercher un lieu' }).getByRole('button', { name: 'Rechercher', exact: true });
}

function options(page: Page) {
	return page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option');
}

function numeroNotice(page: Page) {
	return page.getByText(MESSAGE_NUMERO_PROCHE, { exact: true });
}

function evidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_SEARCH_EVIDENCE_DIR;
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

async function captureBlockBelowHeader(
	page: Page,
	block: Locator,
	width: 1440 | 375,
	name: string,
	testInfo: TestInfo
) {
	await page.setViewportSize({ width, height: 900 });
	const header = page.locator('header.site-header');
	const [headerBox, blockBox] = await Promise.all([header.boundingBox(), block.boundingBox()]);
	expect(headerBox && blockBox, `${name} et en-tête mesurables à ${width} px`).toBeTruthy();
	const height = Math.max(900, Math.ceil(headerBox!.height + blockBox!.height + 32));
	await page.setViewportSize({ width, height });
	await block.evaluate((element, headerHeight) => {
		const documentTop = element.getBoundingClientRect().top + window.scrollY;
		window.scrollTo({ top: Math.max(0, documentTop - Number(headerHeight) - 16), behavior: 'instant' });
	}, headerBox!.height);
	const [positionedHeader, positionedBlock] = await Promise.all([header.boundingBox(), block.boundingBox()]);
	expect(positionedHeader && positionedBlock, `${name} positionné à ${width} px`).toBeTruthy();
	expect(positionedBlock!.y).toBeGreaterThanOrEqual(positionedHeader!.y + positionedHeader!.height);
	expect(positionedBlock!.y + positionedBlock!.height).toBeLessThanOrEqual(height);
	await block.screenshot({ path: evidencePath(testInfo, `${name}-${width}.png`), animations: 'disabled' });
}

async function captureSearchPopup(page: Page, width: 1440 | 375, testInfo: TestInfo) {
	await page.setViewportSize({ width, height: 900 });
	await champ(page).focus();
	const field = champ(page).locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " cs-field ")][1]');
	const listbox = page.getByRole('listbox', { name: 'Lieux proposés' });
	await expect(listbox).toBeVisible();
	const header = page.locator('header.site-header');
	const [headerBox, fieldBox, listBox] = await Promise.all([header.boundingBox(), field.boundingBox(), listbox.boundingBox()]);
	expect(headerBox && fieldBox && listBox, `popup et en-tête mesurables à ${width} px`).toBeTruthy();
	const unionHeight = Math.max(fieldBox!.y + fieldBox!.height, listBox!.y + listBox!.height) - Math.min(fieldBox!.y, listBox!.y);
	const height = Math.max(900, Math.ceil(headerBox!.height + unionHeight + 32));
	await page.setViewportSize({ width, height });
	await field.evaluate((element, headerHeight) => {
		const documentTop = element.getBoundingClientRect().top + window.scrollY;
		window.scrollTo({ top: Math.max(0, documentTop - Number(headerHeight) - 16), behavior: 'instant' });
	}, headerBox!.height);
	const [positionedHeader, positionedField, positionedList] = await Promise.all([
		header.boundingBox(),
		field.boundingBox(),
		listbox.boundingBox()
	]);
	expect(positionedHeader && positionedField && positionedList, `popup positionné à ${width} px`).toBeTruthy();
	const x = Math.min(positionedField!.x, positionedList!.x);
	const y = Math.min(positionedField!.y, positionedList!.y);
	const right = Math.max(positionedField!.x + positionedField!.width, positionedList!.x + positionedList!.width);
	const bottom = Math.max(positionedField!.y + positionedField!.height, positionedList!.y + positionedList!.height);
	expect(y).toBeGreaterThanOrEqual(positionedHeader!.y + positionedHeader!.height);
	expect(bottom).toBeLessThanOrEqual(height);
	await page.screenshot({
		path: evidencePath(testInfo, `champ-popup-${width}.png`),
		clip: { x, y, width: right - x, height: bottom - y },
		animations: 'disabled'
	});
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('Tracer — du champ unique à la page de l’adresse', () => {
	test('« 12 rue des Lilas » sur `/`, option « Adresse » au clavier, page de l’adresse servie', async ({
		page,
		request
	}) => {
		await configure(request, [
			communes({ q: '12 rue des Lilas', limit: '8' }, [okCommunes([])]),
			adresses({ q: '12 rue des Lilas', limite: '8' }, [
				okAdresses([LILAS], ['adresse', 'commune'], HEBERGE, {
					etat: 'exacte',
					numero_demande: 12,
					suffixe_demande: null,
					portee: 'resultats_retournes'
				})
			]),
			// Client navigation (`__data.json`), then the raw HTML read below.
			fiche('85191_0420_00012', [okFiche(IDENTITE_LILAS), okFiche(IDENTITE_LILAS)])
		]);
		await open(page, '/');

		await champ(page).fill('12 rue des Lilas');
		const premiere = options(page).first();
		await expect(premiere.locator('.cs-type')).toHaveText('Adresse');
		await expect(premiere).toContainText('12 Rue des Lilas');
		await expect(premiere).toContainText('85000 La Roche-sur-Yon · Vendée (85)');
		await expect(numeroNotice(page)).toHaveCount(0);

		await champ(page).press('ArrowDown');
		await champ(page).press('Enter');

		await expect(page).toHaveURL(/\/adresse\/85191_0420_00012$/);
		// D-33 (04.8-03): the h1 is the served number and readable street; the postal code and the commune are on the identity line.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
		expect(page.url()).not.toMatch(COORDONNEES);

		const servie = await page.request.get('/adresse/85191_0420_00012');
		expect(servie.status()).toBe(200);
		expect(servie.headers()['x-robots-tag']).toBe('noindex, nofollow');
		const html = await servie.text();
		expect(html).toContain('12 Rue des Lilas, 85000 La Roche-sur-Yon');
		expect(html).not.toMatch(COORDONNEES);
		expect(html.match(/<meta[^>]*name="robots"[^>]*>/g)).toEqual([expect.stringContaining('content="noindex, nofollow"')]);
	});
});

// --- Task 2 — the single field complete, and the typed results page --------------------------

const HORS_COUVERTURE = { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] };

function adresseLilas(numero: number) {
	return { ...LILAS, ban_id: `85191_0420_${String(numero).padStart(5, '0')}`, numero: String(numero) };
}

function commune(code: string, nom: string, hebergement: unknown = HEBERGE, codePostal: string | null = null) {
	return {
		code_insee: code,
		nom,
		code_postal: codePostal,
		departement: code.slice(0, 2),
		population: 1000,
		population_annee: null,
		population_source: 'geo_communes',
		hebergement
	};
}

const RENNES = commune('35238', 'Rennes', HORS_COUVERTURE, '35000');
const PAIX = {
	...LILAS,
	type_resultat: 'voie',
	ban_id: null,
	numero: null,
	nom_voie: 'Rue de la Paix'
};
// The longest street of the fixture: a long name must wrap, never be cut with an ellipsis.
const LONGUE = {
	...LILAS,
	ban_id: '85191_0999_00003',
	numero: '3',
	nom_voie: 'Avenue du Maréchal Jean-Marie Gabriel de Lattre de Tassigny et des Combattants d’Afrique du Nord'
};
const PROCHES_12 = [
	adresseLilas(11),
	adresseLilas(13),
	adresseLilas(15),
	adresseLilas(9),
	{ ...adresseLilas(15), ban_id: '85191_0420_00015_bis', rep: 'bis' }
];
const CORRESPONDANCE_PROCHE_12 = {
	etat: 'proches',
	numero_demande: 12,
	suffixe_demande: null,
	portee: 'resultats_retournes'
};
const BAN_RENNES = {
	...HORS_COUVERTURE,
	territoire: { code_insee: '35238', nom: 'Rennes', departement: { code: '35', nom: 'Ille-et-Vilaine' } }
};
const BAN_35000 = {
	...HORS_COUVERTURE,
	territoire: { code_insee: null, nom: null, departement: { code: '35', nom: 'Ille-et-Vilaine' } }
};
const okCommunesMeta = (donnees: unknown[], ban: unknown): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees, meta: { hebergement: { ban } } }
});
const panne = (status: number, body: unknown = { title: 'x', status, code: 'geocodage_indisponible' }): Reply => ({
	status,
	body
});

function statut(page: Page) {
	return page.getByRole('search', { name: 'Rechercher un lieu' }).getByRole('status');
}

function types(page: Page) {
	return options(page).evaluateAll((items) =>
		items.map((item) => item.querySelector('.cs-type')?.textContent ?? (item.classList.contains('cs-option-panne') ? 'panne' : 'tout'))
	);
}

test.describe('Accueil — le champ unique', () => {
	test('sur-titre, paragraphe, libellé, exemple et action du contrat', async ({ page, request }) => {
		await configure(request, []);
		await open(page, '/');

		// 04.8-07 (UI-SPEC § 6.6): the overline and the lead are replaced by the h1 and the chapô of the v4
		// head; the large field's plain button reads « Rechercher » (§ 3.13).
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Une adresse, en clair');
		await expect(page.locator('.acc-p')).toHaveText(
			'Chaque chiffre vient d’une source publique et dit jusqu’où il vaut : le point de l’adresse, le quartier ou toute la commune.'
		);
		await expect(champ(page)).toHaveAttribute('placeholder', '8 rue de Metz 31000');
		await expect(page.getByRole('main')).not.toContainText(/\binstance\b/i);
		await expect(boutonRechercher(page)).toBeVisible();
		// Without JavaScript the form still reaches the results page with q and departement.
		const formulaire = page.getByRole('search', { name: 'Rechercher un lieu' });
		await expect(formulaire).toHaveAttribute('action', '/communes');
		await expect(formulaire).toHaveAttribute('method', 'get');
	});
});

test.describe('Champ unique — ordre servi, 5 / 3, annonces (D-106)', () => {
	test('« 12 rue des Lilas » : adresses d’abord (5), puis communes (3) ; « Nantes » : l’inverse', async ({
		page,
		request
	}) => {
		const sixAdresses = [1, 2, 3, 4, 5, 6].map(adresseLilas);
		const quatreCommunes = ['85191', '85192', '85193', '85194'].map((code, i) => commune(code, `Commune ${i}`));
		await configure(request, [
			communes({ q: '12 rue des Lilas', limit: '8' }, [okCommunes(quatreCommunes)]),
			adresses({ q: '12 rue des Lilas', limite: '8' }, [okAdresses(sixAdresses, ['adresse', 'commune'])]),
			communes({ q: 'Nantes', limit: '8' }, [okCommunes(quatreCommunes)]),
			adresses({ q: 'Nantes', limite: '8' }, [okAdresses(sixAdresses, ['commune', 'adresse'])])
		]);
		await open(page, '/');

		await champ(page).fill('12 rue des Lilas');
		await expect(options(page)).toHaveCount(9);
		expect(await types(page)).toEqual([...Array(5).fill('Adresse'), ...Array(3).fill('Commune'), 'tout']);
		await expect(statut(page)).toHaveText('3 communes et 5 adresses proposées');
		await expect(numeroNotice(page)).toHaveCount(0);

		await champ(page).fill('Nantes');
		await expect(options(page)).toHaveCount(9);
		expect(await types(page)).toEqual([...Array(4).fill('Commune'), ...Array(4).fill('Adresse'), 'tout']);
	});

	test('annonces accordées : « 1 commune et 3 adresses proposées », « 2 adresses proposées », aucun résultat', async ({
		page,
		request
	}) => {
		await configure(request, [
			communes({ q: '1 rue des Lilas', limit: '8' }, [okCommunes([commune('85191', 'La Roche-sur-Yon')])]),
			adresses({ q: '1 rue des Lilas', limite: '8' }, [okAdresses([1, 2, 3].map(adresseLilas), ['adresse', 'commune'])]),
			communes({ q: '2 rue des Lilas', limit: '8' }, [okCommunes([])]),
			adresses({ q: '2 rue des Lilas', limite: '8' }, [
				okAdresses([2, 12].map(adresseLilas), ['adresse', 'commune'], HEBERGE, null)
			]),
			communes({ q: 'zzzz', limit: '8' }, [okCommunes([])]),
			adresses({ q: 'zzzz', limite: '8' }, [okAdresses([], ['commune', 'adresse'])])
		]);
		await open(page, '/');
		const formulaire = page.getByRole('search', { name: 'Rechercher un lieu' });

		await champ(page).fill('1 rue des Lilas');
		await expect(statut(page)).toHaveText('1 commune et 3 adresses proposées');
		await champ(page).fill('2 rue des Lilas');
		await expect(statut(page)).toHaveText('2 adresses proposées');
		await expect(numeroNotice(page)).toHaveCount(0);
		await champ(page).fill('zzzz');
		await expect(statut(page)).toHaveText('Aucune commune ni adresse proposée pour « zzzz »');
		await expect(formulaire.locator('.cs-notice-title')).toHaveText('Aucune commune ni adresse trouvée pour « zzzz »');
		await expect(formulaire).toContainText(
			"Cela ne veut pas dire qu'elle n'existe pas. Vérifiez l'orthographe, ajoutez la commune ou le code postal, ou retirez le filtre département."
		);
	});

	test('numéro absent des résultats retournés : avertissement borné, ordre API et lien de titre unique', async ({
		page,
		request
	}, testInfo) => {
		test.setTimeout(60_000);
		const proches = okAdresses(PROCHES_12, ['adresse', 'commune'], HEBERGE, CORRESPONDANCE_PROCHE_12);
		await configure(request, [
			communes({ q: '12 rue des Lilas', limit: '8' }, [okCommunes([])]),
			adresses({ q: '12 rue des Lilas', limite: '8' }, [proches, proches, proches]),
			communes({ q: '12 rue des Lilas', limit: '20' }, [okCommunes([]), okCommunes([])])
		]);
		await open(page, '/');
		await champ(page).fill('12 rue des Lilas');

		await expect(numeroNotice(page)).toBeVisible();
		await expect(statut(page)).toContainText(MESSAGE_NUMERO_PROCHE);
		await expect(options(page)).toHaveCount(6);
		await expect(options(page).locator('.cs-option-name')).toHaveText([
			'11 Rue des Lilas',
			'13 Rue des Lilas',
			'15 Rue des Lilas',
			'9 Rue des Lilas',
			'15bis Rue des Lilas'
		]);
		await expect(options(page).locator('.cs-type')).toHaveText(Array(5).fill('Adresse'));

		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await champ(page).focus();
			await champ(page).press('ArrowDown');
			await expect(champ(page)).toHaveAttribute('aria-activedescendant', /.+/);
			const hint = page.locator('[data-number-match="proches"]');
			const listbox = page.getByRole('listbox', { name: 'Lieux proposés' });
			const [hintBox, listBox] = await Promise.all([hint.boundingBox(), listbox.boundingBox()]);
			expect(hintBox && listBox, `avertissement et liste mesurables à ${width} px`).toBeTruthy();
			expect(hintBox!.y + hintBox!.height, `avertissement séparé de la liste à ${width} px`).toBeLessThanOrEqual(
				listBox!.y
			);
			const receivesPointer = await hint.evaluate((element, box) => {
				const hit = document.elementFromPoint(Number(box.x) + Number(box.width) / 2, Number(box.y) + Number(box.height) / 2);
				return hit !== null && (hit === element || element.contains(hit));
			}, hintBox!);
			expect(receivesPointer, `centre de l’avertissement non occulté à ${width} px`).toBe(true);
			for (const target of [champ(page), boutonRechercher(page), ...await options(page).all()]) {
				const box = await target.boundingBox();
				expect(box, `cible du champ mesurable à ${width} px`).toBeTruthy();
				expect(box!.height, `cible du champ haute de 44 px à ${width} px`).toBeGreaterThanOrEqual(44);
			}
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await captureSearchPopup(page, width, testInfo);
		}

		await open(page, '/communes?q=12+rue+des+Lilas');
		await expect(numeroNotice(page)).toBeVisible();
		// 04.8-07 (UI-SPEC § 6.5): the group is the simple panel « Adresses », one row of `ListeLiens` per
		// address (was an `article` card); the row is the single link, its title first.
		const group = page.locator('main section.panneau-simple').filter({ has: page.getByRole('heading', { level: 2, name: /^Adresses?/ }) });
		const cards = group.locator('.liste-liens li');
		await expect(cards).toHaveCount(5);
		await expect(cards.locator('.lien-t')).toHaveText([
			'11 Rue des Lilas',
			'13 Rue des Lilas',
			'15 Rue des Lilas',
			'9 Rue des Lilas',
			'15bis Rue des Lilas'
		]);
		for (let index = 0; index < 5; index += 1) {
			const card = cards.nth(index);
			await expect(card.getByRole('link')).toHaveCount(1);
			const titre = (await card.locator('.lien-t').textContent())!.trim();
			await expect(card.getByRole('link')).toHaveAccessibleName(new RegExp(`^${titre.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
			await expect(card).not.toContainText("Ouvrir la page de l'adresse");
		}
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await captureBlockBelowHeader(page, group, width, 'liste-proches', testInfo);
		}
		await page.setViewportSize({ width: 375, height: 900 });

		const ssr = await page.request.get('/communes?q=12+rue+des+Lilas');
		expect(ssr.status()).toBe(200);
		const html = await ssr.text();
		expect(html).toContain(MESSAGE_NUMERO_PROCHE);
		let previous = -1;
		for (const result of PROCHES_12) {
			const title = `${result.numero}${result.rep ?? ''} Rue des Lilas`;
			const position = html.indexOf(title);
			expect(position, `${title} présent dans le HTML SSR`).toBeGreaterThan(previous);
			previous = position;
			const href = `/adresse/${result.ban_id}`;
			expect(html.split(`href="${href}"`).length - 1, `un lien SSR pour ${title}`).toBe(1);
		}
	});

	test('la qualification du numéro passe dans le flux après Échap, Tab et clic extérieur', async ({
		page,
		request
	}, testInfo) => {
		test.setTimeout(60_000);
		const proches = okAdresses(PROCHES_12, ['adresse', 'commune'], HEBERGE, CORRESPONDANCE_PROCHE_12);
		await configure(request, [
			communes({ q: '12 rue des Lilas', limit: '8' }, [okCommunes([]), okCommunes([])]),
			adresses({ q: '12 rue des Lilas', limite: '8' }, [proches, proches])
		]);

		const overlaps = (a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) =>
			Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)) *
			Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));

		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, '/');
			await page.waitForLoadState('networkidle');
			await page.evaluate(async () => {
				await document.fonts.ready;
				await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
				window.scrollTo({ top: 0, behavior: 'instant' });
			});
			const title = page.getByRole('heading', { level: 1 });
			const arrival = await title.evaluate((element) => {
				const box = element.getBoundingClientRect();
				const header = document.querySelector('header.site-header')!.getBoundingClientRect();
				const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
				return {
					scrollY: window.scrollY,
					top: box.top,
					bottom: box.bottom,
					headerBottom: header.bottom,
					viewportHeight: window.innerHeight,
					hit: hit !== null && element.contains(hit)
				};
			});
			expect(arrival.scrollY, JSON.stringify(arrival)).toBe(0);
			expect(arrival.top, JSON.stringify(arrival)).toBeGreaterThanOrEqual(arrival.headerBottom);
			expect(arrival.bottom, JSON.stringify(arrival)).toBeLessThanOrEqual(arrival.viewportHeight);
			expect(arrival.hit, JSON.stringify(arrival)).toBe(true);
			await page.screenshot({
				path: evidencePath(testInfo, `wr01-arrivee-${width}.png`),
				animations: 'disabled'
			});

			const input = champ(page);
			const form = page.getByRole('search', { name: 'Rechercher un lieu' });
			const submit = form.getByRole('button', { name: 'Rechercher', exact: true });
			// 04.8-07 (UI-SPEC § 6.6, D-34): the examples block is « Pour commencer », generic entries.
			const examples = page.getByRole('region', { name: 'Pour commencer' });
			await input.fill('12 rue des Lilas');
			await expect(options(page)).toHaveCount(6);
			await expect(numeroNotice(page)).toBeVisible();
			const optionNames = await options(page).locator('.cs-option-name').allTextContents();
			const requestsBeforeClosing = await countApiRequests(request, '/v1/adresses/recherche');

			const openedNotice = page.locator('[data-number-match="proches"]');
			const splitWords = await openedNotice.evaluate((root) => {
				const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
				const failures: string[] = [];
				for (let node = walker.nextNode(); node; node = walker.nextNode()) {
					const text = node.textContent ?? '';
					for (const match of text.matchAll(/[\p{L}\p{N}][\p{L}\p{M}\p{N}'’.-]*/gu)) {
						const range = document.createRange();
						range.setStart(node, match.index ?? 0);
						range.setEnd(node, (match.index ?? 0) + match[0].length);
						if (new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size > 1) failures.push(match[0]);
					}
				}
				return failures;
			});
			expect(splitWords, `mots entiers de la qualification à ${width} px`).toEqual([]);
			const openedBox = await openedNotice.boundingBox();
			expect(openedBox, `qualification ouverte mesurable à ${width} px`).toBeTruthy();
			expect(
				await openedNotice.evaluate((element, box) => {
					const hit = document.elementFromPoint(Number(box.x) + Number(box.width) / 2, Number(box.y) + Number(box.height) / 2);
					return hit !== null && (hit === element || element.contains(hit));
				}, openedBox!),
				`qualification ouverte atteignable à ${width} px`
			).toBe(true);
			await page.screenshot({
				path: evidencePath(testInfo, `wr01-ouverte-${width}.png`),
				animations: 'disabled',
				fullPage: true
			});

			const expectClosedFlow = async (mode: string) => {
				await expect(page.getByRole('listbox', { name: 'Lieux proposés' })).toHaveCount(0);
				await expect(numeroNotice(page)).toBeVisible();
				await expect(form.locator('[data-number-match="proches"]')).toHaveCount(1);
				await expect(form.locator('.cs-popup')).toHaveCount(0);
				const flow = await numeroNotice(page).evaluate((element) => ({
					parentIsForm: element.parentElement?.matches('form.cs') ?? false,
					previousIsRow: element.previousElementSibling?.classList.contains('cs-row') ?? false
				}));
				expect(flow, `${mode} replace la qualification après la rangée`).toEqual({
					parentIsForm: true,
					previousIsRow: true
				});
				const [noticeBox, inputBox, submitBox, examplesBox] = await Promise.all([
					numeroNotice(page).boundingBox(),
					input.boundingBox(),
					submit.boundingBox(),
					examples.boundingBox()
				]);
				expect(noticeBox && inputBox && submitBox && examplesBox, `${mode} géométrie mesurable`).toBeTruthy();
				for (const [name, box] of [['champ', inputBox!], ['bouton', submitBox!], ['exemples', examplesBox!]] as const) {
					expect(overlaps(noticeBox!, box), `${mode} : qualification sans intersection avec ${name}`).toBe(0);
				}
				const hit = await numeroNotice(page).evaluate((element, box) => {
					const found = document.elementFromPoint(Number(box.x) + Number(box.width) / 2, Number(box.y) + Number(box.height) / 2);
					return found !== null && (found === element || element.contains(found));
				}, noticeBox!);
				expect(hit, `${mode} : qualification reçoit le pointeur`).toBe(true);
				if (width === 1440) {
					// 04.8-07 (UI-SPEC § 3.13): on the large field the plain button is attached to the field and stays
					// aligned with it. The home page has no departement filter since 09/11.
					const tops = [inputBox!.y, submitBox!.y];
					expect(Math.max(...tops) - Math.min(...tops), `${mode} : contrôles alignés ${tops.join(', ')}`).toBeLessThanOrEqual(1);
				}
			};

			await input.press('Escape');
			await expect(input).toBeFocused();
			await expectClosedFlow('Échap');

			await input.press('ArrowDown');
			await expect(options(page)).toHaveCount(6);
			await input.press('Tab');
			await expect(input).not.toBeFocused();
			const focusedAfterTab = await page.evaluate(() => {
				const active = document.activeElement;
				return active instanceof HTMLElement ? `${active.tagName}#${active.id}` : '';
			});
			expect(focusedAfterTab, 'Tab place le focus sur un élément réel hors du champ').not.toBe('BODY#');
			await expectClosedFlow('Tab');

			await input.focus();
			await expect(options(page)).toHaveCount(6);
			await title.click();
			await expect(input).not.toBeFocused();
			await expectClosedFlow('clic extérieur');

			await input.focus();
			await expect(options(page).locator('.cs-option-name')).toHaveText(optionNames);
			await expect(numeroNotice(page)).toBeVisible();
			expect(await countApiRequests(request, '/v1/adresses/recherche')).toBe(requestsBeforeClosing);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await input.press('Escape');
			await expectClosedFlow('fermeture finale');
			await page.evaluate(async () => {
				await document.fonts.ready;
				await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
				window.scrollTo({ top: 0, behavior: 'instant' });
			});
			await expect(page.getByRole('listbox', { name: 'Lieux proposés' })).toHaveCount(0);
			await page.screenshot({
				path: evidencePath(testInfo, `wr01-fermee-${width}.png`),
				animations: 'disabled',
				fullPage: true
			});
		}
	});
});

test.describe('Champ unique — voie sans numéro (D-107)', () => {
	test('« rue de la Paix » : « Précisez le numéro », pas de navigation, saisie remise sans numéro', async ({
		page,
		request
	}) => {
		await configure(request, [
			communes({ q: 'rue de la Paix', limit: '8' }, [okCommunes([])]),
			adresses({ q: 'rue de la Paix', limite: '8' }, [okAdresses([PAIX], ['adresse', 'commune'], HEBERGE, null)])
		]);
		await open(page, '/');

		await champ(page).fill('rue de la Paix');
		const voie = options(page).first();
		await expect(voie.locator('.cs-type')).toHaveText('Voie');
		await expect(voie.locator('.cs-option-meta')).toHaveText('85000 La Roche-sur-Yon · Vendée (85) · Précisez le numéro');
		await expect(numeroNotice(page)).toHaveCount(0);

		await champ(page).press('ArrowDown');
		await champ(page).press('Enter');

		await expect(page).toHaveURL(/\/$/);
		await expect(champ(page)).toHaveValue('Rue de la Paix, 85000 La Roche-sur-Yon');
		await expect(champ(page)).toBeFocused();
		expect(await champ(page).evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([0, 0]);
		await expect(statut(page)).toHaveText('Ajoutez le numéro de voie au début de la saisie.');
		await expect(options(page)).toHaveCount(0);
	});
});

test.describe('Champ unique — adresses hors couverture (D-105)', () => {
	test('« 1 rue de la Gare Rennes » : encart sous le champ, option commune gardée avec son badge', async ({
		page,
		request
	}, testInfo) => {
		await configure(request, [
			communes({ q: '1 rue de la Gare Rennes', limit: '8' }, [okCommunes([RENNES]), okCommunes([RENNES])]),
			adresses({ q: '1 rue de la Gare Rennes', limite: '8' }, [
				okAdresses([], ['adresse', 'commune'], BAN_RENNES),
				okAdresses([], ['adresse', 'commune'], BAN_RENNES)
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, '/');
			const formulaire = page.getByRole('search', { name: 'Rechercher un lieu' });
			await champ(page).fill('1 rue de la Gare Rennes');
			const encart = formulaire.locator('.cs-notice-adresses');
			await expect(encart.locator('.cs-notice-title')).toHaveText('Rennes (35) : adresses hors couverture');
			await expect(encart).toHaveClass(/cs-notice-warn/);
			await expect(encart).toContainText(
				'Ce site ne couvre pas ce département. Une absence de couverture ne vaut pas zéro.'
			);
			await expect(encart.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute('href', '/datasets');
			await expect(encart).not.toContainText(/\binstance\b/i);
			await expect(numeroNotice(page)).toHaveCount(0);
			const listbox = page.getByRole('listbox', { name: 'Lieux proposés' });
			await expect(listbox).toBeVisible();
			await expect(listbox.locator('.cs-notice-adresses')).toHaveCount(0);
			const rennes = options(page).first();
			await expect(rennes.locator('.cs-type')).toHaveText('Commune');
			await expect(rennes).toContainText('Rennes');
			await expect(rennes.locator('.cs-badge')).toHaveText('Hors couverture · département non hébergé');
			await expect(formulaire.getByText(/Aucune commune ni adresse/)).toHaveCount(0);
			const [noticeBox, listBox] = await Promise.all([encart.boundingBox(), listbox.boundingBox()]);
			expect(noticeBox && listBox, `notice et menu mesurables à ${width} px`).toBeTruthy();
			const intersection = Math.max(
				0,
				Math.min(noticeBox!.x + noticeBox!.width, listBox!.x + listBox!.width) -
					Math.max(noticeBox!.x, listBox!.x)
			) * Math.max(
				0,
				Math.min(noticeBox!.y + noticeBox!.height, listBox!.y + listBox!.height) -
					Math.max(noticeBox!.y, listBox!.y)
			);
			expect(intersection, `notice et menu séparés à ${width} px`).toBe(0);
			const noticeLink = encart.getByRole('link', { name: 'Voir la couverture des données' });
			for (const [name, target] of [
				['titre', encart.locator('.cs-notice-title')],
				['corps', encart.locator('p').nth(1)],
				['lien', noticeLink]
			] as const) {
				await expect(target).toBeVisible();
				const box = await target.boundingBox();
				expect(box, `${name} mesurable à ${width} px`).toBeTruthy();
				const hit = await target.evaluate((element, rect) => {
					const found = document.elementFromPoint(
						Number(rect.x) + Number(rect.width) / 2,
						Number(rect.y) + Number(rect.height) / 2
					);
					return found !== null && (found === element || element.contains(found));
				}, box!);
				expect(hit, `${name} reçoit le pointeur à ${width} px`).toBe(true);
			}
			const linkBox = await noticeLink.boundingBox();
			expect(linkBox!.height, `cible du lien à ${width} px`).toBeGreaterThanOrEqual(44);
			await encart.screenshot({
				path: publicMessagesEvidencePath(testInfo, `recherche-adresse-hors-couverture-${width}.png`),
				animations: 'disabled'
			});
			await champ(page).focus();
			for (let step = 0; step < 4; step += 1) {
				await page.keyboard.press('Tab');
				if (await noticeLink.evaluate((element) => document.activeElement === element)) break;
			}
			await expect(noticeLink).toBeFocused();
			await champ(page).focus();
			await champ(page).press('Escape');
			await expect(listbox).toHaveCount(0);
			await expect(encart).toBeVisible();
			await expect(formulaire.locator('.cs-notice-adresses')).toHaveCount(1);
			if (width === 1440) {
				const controlBoxes = await Promise.all([
					champ(page).boundingBox(),
					formulaire.getByRole('button', { name: 'Rechercher', exact: true }).boundingBox()
				]);
				expect(controlBoxes.every(Boolean), 'contrôles du formulaire mesurables à 1 440 px').toBe(true);
				// 04.8-07 (UI-SPEC § 3.13): field and attached plain button aligned (no departement filter on the
				// home page since 09/11).
				const [champBox, boutonBox] = controlBoxes.map((box) => box!);
				const controlTops = [champBox.y, boutonBox.y];
				expect(
					Math.max(...controlTops) - Math.min(...controlTops),
					`contrôles du formulaire alignés à 1 440 px : ${controlTops.join(', ')}`
				).toBeLessThanOrEqual(1);
			}
			await encart.screenshot({
				path: publicMessagesEvidencePath(testInfo, `recherche-adresse-hors-couverture-fermee-${width}.png`),
				animations: 'disabled'
			});
			// 04.8-07 (UI-SPEC § 6.6, D-34): the examples block is « Pour commencer », generic entries.
			const examples = page.getByRole('region', { name: 'Pour commencer' });
			const examplesTitle = examples.getByRole('heading', { level: 2 });
			const primaryExample = examples.getByRole('link').first();
			let [closedNoticeBox, examplesBox] = await Promise.all([
				encart.boundingBox(),
				examples.boundingBox()
			]);
			expect(closedNoticeBox && examplesBox, `notice fermée et exemples mesurables à ${width} px`).toBeTruthy();
			const closedEvidenceHeight = Math.max(
				900,
				Math.ceil(
					Math.max(
						closedNoticeBox!.y + closedNoticeBox!.height,
						examplesBox!.y + examplesBox!.height
					) + 32
				)
			);
			if (closedEvidenceHeight > 900) {
				await page.setViewportSize({ width, height: closedEvidenceHeight });
				await page.evaluate(
					() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
				);
				[closedNoticeBox, examplesBox] = await Promise.all([
					encart.boundingBox(),
					examples.boundingBox()
				]);
				expect(
					closedNoticeBox && examplesBox,
					`notice fermée et exemples remesurables à ${width} px`
				).toBeTruthy();
			}
			const closedIntersection = Math.max(
				0,
				Math.min(closedNoticeBox!.x + closedNoticeBox!.width, examplesBox!.x + examplesBox!.width) -
					Math.max(closedNoticeBox!.x, examplesBox!.x)
			) * Math.max(
				0,
				Math.min(closedNoticeBox!.y + closedNoticeBox!.height, examplesBox!.y + examplesBox!.height) -
					Math.max(closedNoticeBox!.y, examplesBox!.y)
			);
			const hitTargets = [
				['titre des exemples', examplesTitle],
				['premier exemple', primaryExample]
			] as const;
			const hits = await Promise.all(
				hitTargets.map(async ([name, target]) => {
					const box = await target.boundingBox();
					expect(box, `${name} mesurable à ${width} px`).toBeTruthy();
					const receivesPointer = await target.evaluate((element, rect) => {
						const found = document.elementFromPoint(
							Number(rect.x) + Number(rect.width) / 2,
							Number(rect.y) + Number(rect.height) / 2
						);
						return found !== null && (found === element || element.contains(found));
					}, box!);
					return { name, receivesPointer };
				})
			);
			await page.screenshot({
				path: publicMessagesEvidencePath(
					testInfo,
					`recherche-adresse-hors-couverture-fermee-viewport-${width}.png`
				),
				animations: 'disabled'
			});
			expect(
				closedIntersection,
				`notice fermée ${JSON.stringify(closedNoticeBox)} et exemples ${JSON.stringify(examplesBox)} séparés à ${width} px`
			).toBe(0);
			for (const hit of hits) {
				expect(hit.receivesPointer, `${hit.name} reçoit le pointeur à ${width} px`).toBe(true);
			}
			if (closedEvidenceHeight > 900) {
				await page.setViewportSize({ width, height: 900 });
			}
			// No departement filter on the home page (09/11): nothing to remove in the notice.
			await expect(encart.getByRole('button', { name: 'Retirer le filtre département' })).toHaveCount(0);
		}
		const finalFormulaire = page.getByRole('search', { name: 'Rechercher un lieu' });
		const finalLink = finalFormulaire.getByRole('link', { name: 'Voir la couverture des données' });
		await champ(page).focus();
		// Escape only closes an open list: on a closed one it would clear the query, and the notice with it.
		if (await page.getByRole('listbox', { name: 'Lieux proposés' }).count()) await champ(page).press('Escape');
		for (let step = 0; step < 4; step += 1) {
			await page.keyboard.press('Tab');
			if (await finalLink.evaluate((element) => document.activeElement === element)) break;
		}
		await expect(finalLink).toBeFocused();
		await page.route('**/datasets', (route) =>
			route.fulfill({ status: 200, contentType: 'text/html', body: '<main>Données & méthode</main>' })
		);
		await finalLink.press('Enter');
		await expect(page).toHaveURL('/datasets');
	});

	// C22 (Ianis, 2026-10-08): a postal code out of coverage keeps the phase 3 contract, even when the
	// address search answers out of coverage for its departement (real data, 04-20: 33000).
	test('« 35000 » : le titre du code postal hors couverture (contrat de la phase 3), jamais une commune devinée', async ({
		page,
		request
	}) => {
		await configure(request, [
			communes({ q: '35000', limit: '8' }, [okCommunesMeta([], HORS_COUVERTURE)]),
			adresses({ q: '35000', limite: '8' }, [okAdresses([], ['commune', 'adresse'], BAN_35000)])
		]);
		await open(page, '/');
		const formulaire = page.getByRole('search', { name: 'Rechercher un lieu' });

		await champ(page).fill('35000');
		await expect(formulaire.locator('.cs-notice-title')).toHaveText('Code postal hors couverture');
		await expect(statut(page)).toHaveText('Code postal hors couverture');
		await expect(formulaire.locator('.cs-notice')).toContainText('Ce site ne couvre pas ce département.');
		await expect(formulaire.locator('.cs-notice')).not.toContainText(/\binstance\b/i);
		await expect(formulaire.locator('.cs-notice')).not.toContainText('Rennes');
		await expect(formulaire.getByText(/Aucune commune/)).toHaveCount(0);
	});
});

test.describe('Champ unique — panne partielle (SEM-01)', () => {
	test('adresses en 503, communes servies : options gardées, ligne de tête, « Relancer la recherche »', async ({
		page,
		request
	}) => {
		await configure(request, [
			communes({ q: 'le pin', limit: '8' }, [okCommunes([commune('35999', 'Le Pin')]), okCommunes([commune('35999', 'Le Pin')])]),
			adresses({ q: 'le pin', limite: '8' }, [panne(503), okAdresses([], ['commune', 'adresse'])])
		]);
		await open(page, '/');

		await champ(page).fill('le pin');
		await expect(options(page).first()).toContainText("La recherche d'adresses est momentanément indisponible.");
		await expect(numeroNotice(page)).toHaveCount(0);
		await expect(options(page).first()).toContainText('Relancer la recherche');
		await expect(options(page).nth(1)).toContainText('Le Pin');
		await expect(statut(page)).toHaveText('1 commune proposée');

		await champ(page).press('ArrowDown');
		await champ(page).press('Enter');
		await expect(options(page)).toHaveCount(2);
		expect(await types(page)).toEqual(['Commune', 'tout']);
	});

	test('communes en 503, adresses servies : la ligne nomme les communes ; un 429 reste typé', async ({
		page,
		request
	}) => {
		await configure(request, [
			communes({ q: '12 rue des Lilas', limit: '8' }, [panne(503, { detail: 'x' })]),
			adresses({ q: '12 rue des Lilas', limite: '8' }, [okAdresses([LILAS], ['adresse', 'commune'])]),
			communes({ q: '14 rue des Lilas', limit: '8' }, [okCommunes([])]),
			adresses({ q: '14 rue des Lilas', limite: '8' }, [panne(429, { detail: 'x' })])
		]);
		await open(page, '/');

		await champ(page).fill('12 rue des Lilas');
		await expect(options(page).first()).toContainText('La recherche de communes est momentanément indisponible.');
		await expect(numeroNotice(page)).toHaveCount(0);
		expect(await types(page)).toEqual(['panne', 'Adresse', 'tout']);

		await champ(page).fill('14 rue des Lilas');
		await expect(options(page).first()).toContainText('La limite de requêtes est atteinte.');
		await expect(options(page).first()).not.toContainText('momentanément indisponible');
	});
});

test.describe('Résultats typés — `/communes?q=`', () => {
	test('« 12 rue des Lilas » : groupes h2 dans l’ordre servi, carte adresse vers sa page', async ({ page, request }) => {
		await configure(request, [
			communes({ q: '12 rue des Lilas', limit: '20' }, [okCommunes([commune('85191', 'La Roche-sur-Yon')])]),
			adresses({ q: '12 rue des Lilas', limite: '8' }, [okAdresses([LILAS], ['adresse', 'commune'])])
		]);
		await open(page, '/communes?q=12+rue+des+Lilas');

		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Résultats pour « 12 rue des Lilas »');
		// 04.8-07 (UI-SPEC § 6.5): the typed groups are simple panels in the served order; the address
		// card is a whole-row link (`.lien-t` its title, `.lien-n` its place, was an h3 and its card).
		await expect(page.locator('main section.panneau-simple > h2')).toHaveText(['Adresses', 'Communes']);
		await expect(numeroNotice(page)).toHaveCount(0);
		const carte = page.locator('main section.panneau-simple').nth(0).locator('.liste-liens li').filter({
			has: page.locator('a[href="/adresse/85191_0420_00012"]')
		});
		await expect(carte.locator('.lien-t')).toHaveText('12 Rue des Lilas');
		await expect(carte).toContainText('85000 La Roche-sur-Yon · Vendée (85)');
		const lien = carte.getByRole('link', { name: /^12 Rue des Lilas/ });
		await expect(lien).toHaveCount(1);
		await expect(lien).toHaveAttribute('href', '/adresse/85191_0420_00012');
	});

	// C22: the same postal code on the typed page, both halves out of coverage (the real shape of 33000).
	test('« 35000 » : un seul encart, le titre du code postal hors couverture (contrat de la phase 3)', async ({ page, request }, testInfo) => {
		await configure(request, [
			communes({ q: '35000', limit: '20' }, [okCommunesMeta([], HORS_COUVERTURE), okCommunesMeta([], HORS_COUVERTURE)]),
			adresses({ q: '35000', limite: '8' }, [
				okAdresses([], ['commune', 'adresse'], BAN_35000),
				okAdresses([], ['commune', 'adresse'], BAN_35000)
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			await open(page, '/communes?q=35000');
			const resultats = page.getByRole('region', { name: 'Résultats' });
			await expect(resultats.getByRole('heading', { level: 2 })).toHaveText('Code postal hors couverture');
			await expect(resultats.locator('.communes-notice')).toHaveClass(/communes-notice-warn/);
			await expect(resultats).toContainText('Ce site ne couvre pas ce département.');
			await expect(resultats).not.toContainText(/\binstance\b/i);
			await expect(resultats).not.toContainText('Ille-et-Vilaine');
			await expect(resultats.getByText(/Aucune commune/)).toHaveCount(0);
			await resultats.screenshot({
				path: publicMessagesEvidencePath(testInfo, `resultats-code-postal-hors-couverture-${width}.png`),
				animations: 'disabled'
			});
		}
	});

	test('« zzzz » : le contrat « Aucun résultat », aucun groupe', async ({ page, request }) => {
		await configure(request, [
			communes({ q: 'zzzz', limit: '20' }, [okCommunes([])]),
			adresses({ q: 'zzzz', limite: '8' }, [okAdresses([], ['commune', 'adresse'])])
		]);
		await open(page, '/communes?q=zzzz');

		const resultats = page.getByRole('region', { name: 'Résultats' });
		// D8 (2026-11-10, agreed by the orchestrator): the « Aucun résultat » is the Notice of the fiche, its h2
		// the one heading of the results, announced by the status role of the panel (was the state line
		// `p.r-etat`, no h2); no group means neither a panel nor a group notice.
		await expect(resultats.getByRole('heading', { level: 2 })).toHaveCount(1);
		await expect(resultats.getByRole('status').getByRole('heading', { level: 2 })).toHaveText('Aucune commune ni adresse trouvée pour « zzzz »');
		await expect(resultats).toContainText("Cela ne veut pas dire qu'elle n'existe pas.");
		await expect(page.locator('.communes-group, main section.panneau-simple')).toHaveCount(0);
	});
});

for (const largeur of [375, 768, 1280] as const) {
	test.describe(`Champ unique à ${largeur} px (UI-01)`, () => {
		test.use({ viewport: { width: largeur, height: 800 } });

		test('listbox à la largeur du champ, aucun débordement, voie longue sans ellipse', async ({ page, request }) => {
			await configure(request, [
				communes({ q: '3 avenue', limit: '8' }, [okCommunes([commune('85191', 'La Roche-sur-Yon')])]),
				adresses({ q: '3 avenue', limite: '8' }, [okAdresses([LONGUE, LILAS], ['adresse', 'commune'])])
			]);
			await open(page, '/');

			await champ(page).fill('3 avenue');
			await expect(options(page)).toHaveCount(4);
			const listbox = page.getByRole('listbox', { name: 'Lieux proposés' });
			const popup = listbox.locator('..');
			const champBox = await champ(page).boundingBox();
			const popupBox = await popup.boundingBox();
			const listeBox = await listbox.boundingBox();
			expect(champBox && popupBox && listeBox, 'champ, popup et liste mesurés').toBeTruthy();
			// The popup now owns the border around the message and options; the listbox used to own it.
			expect(Math.abs(popupBox!.width - champBox!.width)).toBeLessThanOrEqual(1);
			expect(Math.abs(popupBox!.x - champBox!.x)).toBeLessThanOrEqual(1);
			expect(listeBox!.x).toBeGreaterThanOrEqual(popupBox!.x);
			expect(listeBox!.x + listeBox!.width).toBeLessThanOrEqual(popupBox!.x + popupBox!.width);
			const mesure = await page.evaluate(() => ({
				scroll: document.documentElement.scrollWidth,
				inner: window.innerWidth
			}));
			expect(mesure.scroll).toBeLessThanOrEqual(mesure.inner);
			// The long street wraps inside its option: whole text, no ellipsis, no clipped box.
			const nom = options(page).first().locator('.cs-option-name');
			await expect(nom).toHaveText(`3 ${LONGUE.nom_voie}`);
			const coupe = await nom.evaluate((el) => ({
				ellipse: getComputedStyle(el).textOverflow === 'ellipsis',
				deborde: el.scrollWidth > el.clientWidth + 1
			}));
			expect(coupe).toEqual({ ellipse: false, deborde: false });
		});
	});
}

// --- Task 3 — the states of the address page (SEM-01, D-102, D-105, D-107) -------------------

const IDENTITE_RENNES = {
	etat: 'etablie',
	ban_id: '35238_1234_00001',
	libelle: null,
	numero: null,
	rep: null,
	nom_voie: null,
	code_postal: null,
	commune: { code_insee: '35238', nom: 'Rennes' },
	departement: { code: '35', nom: 'Ille-et-Vilaine' },
	parcelle: null
};

// The longest label of the fixture: it must wrap at 320 px and at 200 % zoom, never be cut.
const LIBELLE_LONG =
	'3 Avenue du Maréchal Jean-Marie Gabriel de Lattre de Tassigny et des Combattants d’Afrique du Nord, 85000 La Roche-sur-Yon';
// D-33 (04.8-03): the h1 of that address, number then readable street; the postal code and the commune moved to
// the identity line under it.
const H1_LONG = '3 avenue du Maréchal Jean-Marie Gabriel de Lattre de Tassigny et des Combattants d’Afrique du Nord';
const IDENTITE_LONGUE = {
	...IDENTITE_LILAS,
	ban_id: '85191_0999_00003',
	libelle: LIBELLE_LONG,
	numero: '3',
	nom_voie: 'Avenue du Maréchal Jean-Marie Gabriel de Lattre de Tassigny et des Combattants d’Afrique du Nord'
};

const probleme = (status: number, code: string, title: string): Reply => ({ status, body: { title, status, code } });

/** Navigates to an address page; the HTTP status of the document is returned. */
async function ouvrirAdresse(page: Page, url: string): Promise<number> {
	const hydrated = ACCOUNTS_ENABLED
		? page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me')
		: null;
	const reponse = await page.goto(url);
	if (hydrated) await hydrated;
	return reponse?.status() ?? 0;
}

/** h1 measured: whole text, wrapped, never cut; the document does not overflow the window. */
async function mesurerTitre(page: Page) {
	return page.evaluate(() => {
		const h1 = document.querySelector('h1')!;
		const style = getComputedStyle(h1);
		return {
			texte: h1.textContent,
			hauteur: Math.round(h1.getBoundingClientRect().height),
			lignes: Math.round(h1.getBoundingClientRect().height / parseFloat(style.lineHeight)),
			coupe: style.textOverflow === 'ellipsis' || h1.scrollWidth > h1.clientWidth + 1,
			scroll: document.documentElement.scrollWidth,
			inner: window.innerWidth
		};
	});
}

test.describe('Page adresse — états servis', () => {
	test('hors couverture : 200, h1 « Adresse à Rennes (35) », encart `warn`, ses deux liens, aucun repère', async ({
		page,
		request
	}, testInfo) => {
		await configure(request, [
			fiche('35238_1234_00001', [
				okFiche(IDENTITE_RENNES, { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' }),
				okFiche(IDENTITE_RENNES, { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' })
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			expect(await ouvrirAdresse(page, '/adresse/35238_1234_00001')).toBe(200);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText('Adresse à Rennes (35)');
			const encart = page.locator('.notice.notice-warn');
			await expect(encart.getByRole('heading', { level: 2 })).toHaveText('Adresse hors couverture');
			await expect(encart).toContainText(
				'Ce site ne couvre pas ce département. Une absence de couverture ne vaut pas zéro.'
			);
			await expect(encart.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute('href', '/datasets');
			await expect(encart).not.toContainText(/\binstance\b/i);
			await expect(encart.getByRole('link', { name: 'Voir la fiche de Rennes' })).toHaveAttribute('href', '/commune/35238');
			await expect(page.locator('[data-kpi]')).toHaveCount(0);
			await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
			await page.getByRole('main').screenshot({
				path: publicMessagesEvidencePath(testInfo, `adresse-hors-couverture-${width}.png`),
				animations: 'disabled'
			});
		}
	});

	test('inconnue : 404 « Adresse inconnue », la phrase du contrat, « Revenir à la recherche »', async ({ page, request }, testInfo) => {
		await configure(request, [
			fiche('85191_9999_00001', [
				probleme(404, 'adresse_inconnue', 'Adresse inconnue'),
				probleme(404, 'adresse_inconnue', 'Adresse inconnue')
			])
		]);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: 900 });
			expect(await ouvrirAdresse(page, '/adresse/85191_9999_00001')).toBe(404);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText('Adresse inconnue');
			await expect(page.getByText("Aucune adresse ne porte cet identifiant dans la base d'adresses de ce site.")).toBeVisible();
			await expect(page.getByRole('main')).not.toContainText(/\binstance\b/i);
			await expect(page.getByRole('main').getByRole('link', { name: 'Revenir à la recherche' })).toHaveAttribute('href', '/');
			await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
			await page.getByRole('main').screenshot({
				path: publicMessagesEvidencePath(testInfo, `adresse-inconnue-${width}.png`),
				animations: 'disabled'
			});
		}
	});

	test('voie ouverte par un lien : 404 « Précisez le numéro », jamais une page de rue (D-107)', async ({ page, request }) => {
		await configure(request, [fiche('85191_0420', [probleme(404, 'voie_sans_numero', 'Voie sans numero')])]);

		expect(await ouvrirAdresse(page, '/adresse/85191_0420')).toBe(404);

		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Précisez le numéro');
		await expect(
			page.getByText("Une page d'adresse porte sur un numéro précis. Choisissez le numéro dans la recherche.")
		).toBeVisible();
	});

	test('panne : 503, un encart, aucune valeur, « Recharger la fiche » relit l’adresse', async ({ page, request }) => {
		await configure(request, [
			fiche('85191_0420_00012', [probleme(503, 'adresse_indisponible', 'Adresse momentanement indisponible'), okFiche(IDENTITE_LILAS)])
		]);

		expect(await ouvrirAdresse(page, '/adresse/85191_0420_00012')).toBe(503);

		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Adresse momentanément indisponible');
		const alerte = page.getByRole('alert');
		await expect(alerte.getByRole('heading', { level: 2 })).toHaveText(
			'Les données de cette adresse sont momentanément indisponibles'
		);
		await expect(alerte).toContainText(
			"Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète."
		);
		await expect(page.locator('.notice')).toHaveCount(1);
		await expect(page.locator('.adresse-identity')).toHaveCount(0);
		await expect(page.getByText('Rue des Lilas')).toHaveCount(0);

		await alerte.getByRole('button', { name: 'Recharger la fiche' }).click();

		// D-33 (04.8-03): the h1 is the served number and readable street; the postal code and the commune are on the identity line.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		await expect(page.locator('.notice')).toHaveCount(0);
	});

	test('409 et 429 : titrés par panne.ts, jamais « momentanément indisponible »', async ({ page, request }) => {
		await configure(request, [
			fiche('85191_0420_00012', [{ status: 409, body: { detail: 'texte serveur' } }]),
			fiche('85191_0420_00014', [{ status: 429, body: { detail: 'texte serveur' } }])
		]);

		expect(await ouvrirAdresse(page, '/adresse/85191_0420_00012')).toBe(200);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Demande refusée');
		await expect(page.getByRole('alert')).toContainText("La demande n'a pas pu être traitée (erreur 409).");
		await expect(page.getByRole('alert').getByRole('button', { name: 'Recharger la fiche' })).toBeVisible();

		await ouvrirAdresse(page, '/adresse/85191_0420_00014');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Trop de requêtes pour le moment');
		await expect(page.getByText(/momentanément indisponible/)).toHaveCount(0);
		await expect(page.getByText('texte serveur')).toHaveCount(0);
	});
});

test.describe('Page adresse — 320 px (UI-01)', () => {
	test.use({ viewport: { width: 320, height: 800 } });

	test('h1 d’adresse longue : retour à la ligne, jamais tronqué, aucun débordement', async ({ page, request }) => {
		await configure(request, [fiche('85191_0999_00003', [okFiche(IDENTITE_LONGUE)])]);

		expect(await ouvrirAdresse(page, '/adresse/85191_0999_00003')).toBe(200);

		const mesure = await mesurerTitre(page);
		console.log(`RELEVE 320px h1_adresse_longue hauteur=${mesure.hauteur}px lignes=${mesure.lignes} scrollWidth=${mesure.scroll} innerWidth=${mesure.inner}`);
		expect(mesure.texte).toBe(H1_LONG);
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		expect(mesure.coupe).toBe(false);
		expect(mesure.lignes).toBeGreaterThan(1);
		expect(mesure.scroll).toBeLessThanOrEqual(mesure.inner);
	});
});

test('Page adresse — zoom navigateur réel 200 % : h1 long entier, aucun débordement', async ({}, testInfo) => {
	// Same method as fiche-grid.spec.ts: a local MV3 extension sets Chromium's real tab zoom (CSS
	// zoom and CDP pageScaleFactor do not reproduce the browser media-query behaviour).
	const extension = mkdtempSync(join(tmpdir(), 'dc0417-zoom-extension-')); // Outside Vite's watched workspace.
	writeFileSync(
		`${extension}/manifest.json`,
		JSON.stringify({ manifest_version: 3, name: 'Local address zoom test', version: '1.0', permissions: ['tabs'], background: { service_worker: 'background.js' } })
	);
	writeFileSync(`${extension}/background.js`, 'chrome.runtime.onInstalled.addListener(() => {});');
	const context = await chromium.launchPersistentContext('', {
		channel: 'chromium',
		headless: true,
		baseURL: E2E_APP_ORIGIN,
		viewport: { width: 1280, height: 900 },
		args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
	});
	try {
		const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
		const page = await context.newPage();
		await installLocalNetworkGuard(page);
		await configure(page.request, [fiche('85191_0999_00003', [okFiche(IDENTITE_LONGUE)])]);
		expect(await ouvrirAdresse(page, '/adresse/85191_0999_00003')).toBe(200);

		const zoom = await worker.evaluate(async (appOrigin) => {
			const api = (globalThis as unknown as { chrome: { tabs: { query: (q: unknown) => Promise<{ id: number }[]>; setZoom: (id: number, z: number) => Promise<void>; getZoom: (id: number) => Promise<number> } } }).chrome;
			const tabs = await api.tabs.query({ url: `${appOrigin}/*` });
			if (tabs.length !== 1) throw new Error('Expected one local test tab');
			await api.tabs.setZoom(tabs[0].id, 2);
			return api.tabs.getZoom(tabs[0].id);
		}, E2E_APP_ORIGIN);
		expect(zoom).toBe(2);
		await expect.poll(() => page.evaluate(() => Math.abs(innerWidth - 640))).toBeLessThanOrEqual(0.5);

		const mesure = await mesurerTitre(page);
		console.log(`RELEVE zoom200 h1_adresse_longue hauteur=${mesure.hauteur}px lignes=${mesure.lignes} scrollWidth=${mesure.scroll} innerWidth=${mesure.inner}`);
		writeFileSync(testInfo.outputPath('zoom-200.json'), JSON.stringify({ zoom, ...mesure }, null, 2));
		expect(mesure.texte).toBe(H1_LONG);
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		expect(mesure.coupe).toBe(false);
		expect(mesure.scroll).toBeLessThanOrEqual(mesure.inner);
		await expectSourceContractMet(page.request);
		expectNoUnexpectedOffLocalRequest(page);
	} finally {
		await context.close();
	}
});
