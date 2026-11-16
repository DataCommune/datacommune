import { test, expect } from '@playwright/test';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';

// Review 2026-11-10, finding R-05 (red on purpose, no fix here).
//
// « Comparer avec une autre commune » on a commune fiche adds the commune to the selection of the
// tab and opens the comparator (commune/[code]/+page.svelte:419-423). With four communes already
// selected, `comparisonSelection.add` answers `{ ajoute: false, raison: 'limite' }` and the handler
// drops that answer: the visitor lands on the comparison of the four other communes, the commune
// they came from is absent and nothing says why. The directory (`AjoutComparaison.svelte`) words
// the same refusal. Replayed on datacommune.fr on 2026-11-09 from /commune/85047.

const MOCK_COMMUNE_85047 = {
	statut: 'ok',
	donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC }],
	meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
};
const DEJA_CHOISIES = ['44109', '85191', '85194', '44184'];

test('R-05 — sélection pleine : la fiche dit pourquoi sa commune n’est pas ajoutée, ou l’ajoute', async ({ page }) => {
	await page.addInitScript((codes) => sessionStorage.setItem('dc:comparaison', JSON.stringify(codes)), DEJA_CHOISIES);
	await configureSsrSource(page.request, [
		{ method: 'GET', pathname: '/v1/commune', query: { code_insee: '85047' }, minCalls: 1, maxCalls: 1, responses: [{ status: 200, body: MOCK_COMMUNE_85047 }] }
	]);
	// The comparison itself is not the subject: whatever the comparator reads is left unanswered.
	await page.route('**/v1/communes/comparaison**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto('/commune/85047');
	await hydratee;

	await page.getByRole('link', { name: 'Comparer avec une autre commune' }).click();
	await page.waitForLoadState('networkidle');

	const dansLeLien = new URL(page.url()).searchParams.get('communes')?.split(',').includes('85047') ?? false;
	const refusDit = await page.getByText(/quatre communes|4 communes au (maximum|plus)|maximum/i).first().isVisible().catch(() => false);
	expect(dansLeLien || refusDit, `arrivée sur ${page.url()} sans Challans et sans un mot`).toBe(true);
});
