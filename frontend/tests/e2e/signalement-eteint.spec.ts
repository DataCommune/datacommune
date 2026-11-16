// Error report switched off, the default of every build that does not set VITE_SIGNALEMENT=true
// (framing of 2026-11-10): the branch can be merged and published without showing anything. The page
// does not exist, the form cannot be posted, the footer keeps its mailto and the information panel
// ends where it ended before. The switched-on behaviour is in `signalement.spec.ts`, run apart.
import { test, expect, type APIRequestContext } from '@playwright/test';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from './fixtures/commune-indicators';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

async function configurer(request: APIRequestContext) {
	const body = { statut: 'ok', donnees: [COMMUNE_85191_OBSERVED], meta: { source: COMMUNE_85191_SOURCES } };
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				{
					method: 'GET',
					pathname: '/v1/commune',
					query: { code_insee: '85191' },
					minCalls: 0,
					maxCalls: 20,
					responses: Array.from({ length: 20 }, () => ({ status: 200, body }))
				}
			]
		}
	});
	expect(res.ok()).toBe(true);
}

async function depots(request: APIRequestContext): Promise<number> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	const { requests } = (await res.json()) as { requests: { pathname: string }[] };
	return requests.filter((r) => r.pathname === '/v1/signalements').length;
}

test.beforeEach(async ({ request }) => configurer(request));

test('la page /signaler n’existe pas : 404 à la lecture comme à l’envoi, rien ne part vers l’API', async ({ page, request, baseURL }) => {
	const lecture = await page.goto('/signaler?de=%2Fcommune%2F85191');
	expect(lecture?.status()).toBe(404);
	await expect(page.locator('form[method="POST"]')).toHaveCount(0);

	const envoi = await request.post('/signaler', {
		headers: { origin: baseURL! },
		form: { commentaire: 'Un signalement qui ne doit aller nulle part.' }
	});
	expect(envoi.status()).toBe(404);
	expect(await depots(request)).toBe(0);
});

test('le pied garde son mailto et le volet d’information ne propose aucun signalement', async ({ page }) => {
	// The « i » opens once the page is hydrated: the session is read then.
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto('/commune/85191');
	await hydratee;
	const pied = page.getByRole('contentinfo');
	await expect(pied.getByRole('link', { name: 'Signaler une erreur' })).toHaveAttribute('href', 'mailto:signalement@datacommune.fr');
	await expect(page.locator('a[href^="/signaler"]')).toHaveCount(0);

	await page.getByRole('button', { name: /^(Détail|Source et définition) : / }).first().click();
	const volet = page.locator('dialog[open]');
	await expect(volet).toBeVisible();
	await expect(volet.locator('a[href^="/signaler"]')).toHaveCount(0);
	await expect(volet.getByText('Signaler', { exact: false })).toHaveCount(0);
});
