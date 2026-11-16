// R-07 (2026-11-10): the first view of an address never opened. A live source that takes 6 s used to
// hold the fiche 5 s and then be written « Source indisponible ». The fiche now asks the report with a
// short wait: the criterion of the slow source says « Lecture en cours », and its value arrives without a
// reload when the source has answered. Without JavaScript, the reload link of the mention brings it.
//
// The slow source is simulated by the synthetic API: the report asked with `attente=courte` is served at
// once with the criterion `en_cours`; the one asked with `attente=complete` is held behind a gate that
// the test releases 6 s later. What the API itself does with a slow producer (the short wait, the shared
// reads, the end of the wait) is covered by `api/backend/tests/test_rapport_en_cours.py`; the weekly
// allowance counted once by `en-cours.test.ts`, on the real counter. Here: one session, one follow-up.
// Headless runner, isolated context, every off-local request refused (AGENTS.md).
import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { PREMIERE_PAGE_DECLAREES } from './fixtures/adresse-ventes';
import { attendreAucuneCarteNonReconnue, repondreCarteStatique } from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';

interface Theme {
	id: string;
	titre: string;
	verdict: string;
	etat: string;
	granularite: unknown;
	[cle: string]: unknown;
}
interface Entree {
	banId: string;
	rapport: { donnees: { resume: Record<string, { theme: string }[]>; themes: Theme[]; lectures_en_cours?: string[] } };
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}

const BASE = JSON.parse(readFileSync(new URL('./fixtures/rapport-43-themes.json', import.meta.url), 'utf8')) as Entree;
const LENT = 'inondation';
const AMONT_LENT_MS = 6_000;
const PORTE = 'amont-lent';
const VERDICT_LU = BASE.rapport.donnees.themes.find((t) => t.id === LENT)!.verdict;
const LISTES_DU_RESUME = ['points_adresse', 'points_commune', 'rassurants', 'rassurants_adresse', 'rassurants_commune', 'non_concernes', 'partiels', 'sans_valeur'];

/** The report of the first answer: the slow criterion is still being read, as the API serves it. */
function premiereReponse(): Entree['rapport'] {
	const rapport = structuredClone(BASE.rapport);
	const d = rapport.donnees;
	const i = d.themes.findIndex((t) => t.id === LENT);
	const lu = d.themes[i];
	expect(lu.etat, 'the fixture serves the slow criterion with a value').toBe('ok');
	d.themes[i] = {
		...lu,
		etat: 'en_cours',
		ton: 'info',
		verdict: 'La source n’a pas encore répondu.',
		accroche: 'Lecture en cours',
		chiffres: [],
		details: {},
		motif: null,
		etat_libelle: null,
		pannes_partielles: []
	};
	for (const liste of LISTES_DU_RESUME) d.resume[liste] = d.resume[liste].filter((p) => p.theme !== LENT);
	d.resume.en_cours = [{ theme: LENT, titre: lu.titre, verdict: 'La source n’a pas encore répondu.', granularite: lu.granularite } as never];
	d.lectures_en_cours = [LENT];
	return rapport;
}

const lecture = (pathname: string, query: Record<string, string>, responses: unknown[], minCalls = 0) => ({
	method: 'GET',
	pathname,
	query,
	minCalls,
	maxCalls: responses.length,
	responses
});
const servi = (body: unknown, gate?: string) => ({ status: 200, body, ...(gate ? { gate } : {}) });
const fois = (n: number, body: unknown) => Array.from({ length: n }, () => servi(body));

async function configurer(request: APIRequestContext, rapports: unknown[]) {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				...rapports,
				lecture(`/v1/adresse/${BASE.banId}/dpe`, { page: '1' }, fois(4, BASE.documents)),
				lecture(`/v1/adresse/${BASE.banId}/ventes`, { page: '1' }, fois(4, BASE.ventes)),
				lecture(`/v1/adresse/${BASE.banId}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, fois(4, BASE.ventesAdresse))
			]
		}
	});
	expect(res.ok(), await res.text()).toBe(true);
}

async function audit(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	return (await res.json()) as { requests: { pathname: string; query: Record<string, string> }[]; violations: unknown[]; unmetExpectations: unknown[] };
}

const lecturesDuRapport = async (request: APIRequestContext) =>
	(await audit(request)).requests.filter((r) => r.pathname === cheminRapport(BASE.banId)).map((r) => r.query.attente ?? '');

const ligne = (page: Page) => page.locator(`#critere-${LENT}`);
const mention = (page: Page) => page.locator('.lectures-en-cours[role="status"]');

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
	// The map of the head comes from our own proxy: not the subject here (its two pictures answer 503).
	await repondreCarteStatique(page, { statut: 503 });
});

test.afterEach(async ({ page, request }) => {
	const lu = await audit(request);
	expect(lu.violations, JSON.stringify(lu.violations)).toEqual([]);
	expect(lu.unmetExpectations, JSON.stringify(lu.unmetExpectations)).toEqual([]);
	expectNoUnexpectedOffLocalRequest(page);
	attendreAucuneCarteNonReconnue(page);
});

test('une source à 6 s : la fiche arrive avant elle, dit « Lecture en cours », puis affiche la valeur sans rechargement', async ({ page, request, context }) => {
	test.setTimeout(60_000);
	// A first visit compiles the route in the dev server: not the wait measured below.
	await configurer(request, [lecture(cheminRapport(BASE.banId), REQUETE_RAPPORT, fois(1, BASE.rapport), 1)]);
	await page.goto(`/adresse/${BASE.banId}`);
	await expect(ligne(page)).toHaveAttribute('data-etat', 'ok');
	await context.clearCookies();

	await configurer(request, [
		lecture(cheminRapport(BASE.banId), REQUETE_RAPPORT, [servi(premiereReponse())], 1),
		lecture(cheminRapport(BASE.banId), { attente: 'complete' }, [servi(BASE.rapport, PORTE)], 1)
	]);
	const debut = Date.now();
	const hydratee = page.waitForResponse((r) => new URL(r.url()).pathname === '/auth/me');
	const suite = page.waitForRequest((r) => new URL(r.url()).pathname === `/adresse/${BASE.banId}/suite`);
	const document = await page.goto(`/adresse/${BASE.banId}`, { waitUntil: 'commit' });
	const premiereReponseMs = Date.now() - debut;
	expect(document?.status()).toBe(200);
	// Never stored: a partial answer must not be kept by a cache as the fiche of the address.
	expect(document?.headers()['cache-control']).toBe('private, no-store');
	test.info().annotations.push({ type: 'premiere-reponse-ms', description: String(premiereReponseMs) });
	// The fiche does not wait for the slow source (6 s, still held): its first byte comes well before.
	expect(premiereReponseMs, 'premier octet de la fiche').toBeLessThan(AMONT_LENT_MS / 2);

	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await expect(ligne(page)).toHaveAttribute('data-etat', 'en_cours');
	await expect(ligne(page)).toContainText('Lecture en cours');
	await expect(ligne(page)).not.toContainText('Source indisponible');
	await expect(mention(page)).toContainText('1 critère attend encore la réponse de sa source.');
	await expect(mention(page).getByRole('link', { name: 'Recharger la fiche' })).toHaveAttribute('href', `/adresse/${BASE.banId}`);
	// The other criteria are served: 43 rows, the slow one among them, at its place.
	await expect(page.locator('li[id^="critere-"]')).toHaveCount(43);
	const placeAvant = await page.locator('li[id^="critere-"]').evaluateAll((els) => els.map((e) => e.id));

	await hydratee;
	await suite;
	await page.evaluate(() => ((window as unknown as { __memeVue: boolean }).__memeVue = true));
	const session = (await context.cookies()).filter((c) => c.name === 'dc_session');
	expect(session).toHaveLength(1);

	// The source answers 6 s after the view started.
	await expect.poll(() => Date.now() - debut, { timeout: AMONT_LENT_MS + 5_000 }).toBeGreaterThanOrEqual(AMONT_LENT_MS);
	await expect(ligne(page)).toHaveAttribute('data-etat', 'en_cours');
	const relache = await request.post(`${E2E_SOURCE_ORIGIN}/__release`, { data: { gate: PORTE } });
	expect((await relache.json()).count).toBe(1);

	await expect(ligne(page)).toHaveAttribute('data-etat', 'ok');
	await expect(ligne(page)).toContainText(VERDICT_LU);
	await expect(ligne(page)).not.toContainText('Lecture en cours');
	await expect(mention(page)).toContainText('Toutes les sources ont répondu.');
	await expect(mention(page).getByRole('link')).toHaveCount(0);
	// No reload: the document is the same, the rows kept their place.
	expect(await page.evaluate(() => (window as unknown as { __memeVue?: boolean }).__memeVue)).toBe(true);
	expect(await page.locator('li[id^="critere-"]').evaluateAll((els) => els.map((e) => e.id))).toEqual(placeAvant);
	// One short read, one follow-up; the same session, so the same fiche for the weekly allowance.
	expect(await lecturesDuRapport(request)).toEqual(['courte', 'complete']);
	expect((await context.cookies()).filter((c) => c.name === 'dc_session')).toEqual(session);
});

test.describe('sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('la mention porte un lien de rechargement, qui suffit à lire la valeur ; aucune suite n’est demandée', async ({ page, request }) => {
		await configurer(request, [lecture(cheminRapport(BASE.banId), REQUETE_RAPPORT, [servi(premiereReponse()), servi(BASE.rapport)], 2)]);
		await page.goto(`/adresse/${BASE.banId}`);
		await expect(ligne(page)).toHaveAttribute('data-etat', 'en_cours');
		await expect(ligne(page)).toContainText('Lecture en cours');
		await expect(mention(page)).toContainText('1 critère attend encore la réponse de sa source.');

		await mention(page).getByRole('link', { name: 'Recharger la fiche' }).click();
		await expect(ligne(page)).toHaveAttribute('data-etat', 'ok');
		await expect(ligne(page)).toContainText(VERDICT_LU);
		await expect(mention(page)).toHaveCount(0);
		expect(await lecturesDuRapport(request)).toEqual(['courte', 'courte']);
	});
});
