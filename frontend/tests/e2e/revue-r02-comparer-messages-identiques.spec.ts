import { test, expect } from '@playwright/test';

// Review 2026-11-10, finding R-02 (red on purpose, no fix here).
//
// `/comparer` keys its « Lien altéré » notices by their text (`{#each … (message)}`,
// comparer/+page.svelte:620 and :627). A link that produces the same message twice (the same
// malformed code twice, or the same commune three times) makes Svelte throw `each_key_duplicate`
// while hydrating: the server render is torn down and the visitor is left with a blank page (only
// the skip link remains). Replayed on datacommune.fr on 2026-11-09 with both links below.
// No source is read: none of these links holds two valid codes.

for (const [cas, requete] of [
	['le même code mal formé deux fois', 'communes=ABC,ABC'],
	['la même commune trois fois', 'communes=44109,44109,44109'],
	['la même clé d’adresse mal formée deux fois', 'adresses=x,x'],
	['le même numéro DPE mal formé deux fois', 'dpe=x,x']
] as const) {
	test(`R-02 — /comparer?${requete} (${cas}) : la page reste affichée après l’hydratation`, async ({ page }) => {
		const erreurs: string[] = [];
		page.on('pageerror', (erreur) => erreurs.push(erreur.message));
		// A commune alone reads its name in the referential; unanswered here, the page names it by its code.
		await page.route('**/v1/communes**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));
		// No wait on `/auth/me` here: the page that throws while hydrating never reads the account.
		await page.goto(`/comparer?${requete}`);
		await page.waitForLoadState('networkidle');
		expect(erreurs, 'exception thrown while hydrating').toEqual([]);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		await expect(page.getByText('Lien altéré')).toBeVisible();
	});
}
