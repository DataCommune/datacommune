import { expect, test, type Page } from '@playwright/test';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';

const ADMIN = {
	id: 99101,
	email: 'admin-preview@example.invalid',
	name: 'Admin Preview',
	is_verified: true,
	is_admin: true,
	created_at: '2026-09-09T00:00:00Z'
};

const NON_ADMIN = { ...ADMIN, id: 99102, email: 'user-preview@example.invalid', is_admin: false };
const ADDRESS_LABEL = '7 rue Synthétique, 44000 Nantes';
const PRIVATE_DOCUMENT_REF = 'opaque-private-doc-alpha';

const inventory = {
	version: 2,
	state: 'ready',
	context_ref: 'synthetic-context-preview',
	items: [
		{
			document_ref: PRIVATE_DOCUMENT_REF,
			type_batiment: 'Appartement',
			surface_habitable: 48,
			etiquette_dpe: 'C',
			etiquette_ges: 'B',
			date_etablissement_dpe: '2024-02-03',
			date_fin_validite_dpe: '2034-02-02'
		},
		{
			document_ref: 'opaque-private-doc-beta',
			type_batiment: null,
			surface_habitable: null,
			etiquette_dpe: null,
			etiquette_ges: null,
			date_etablissement_dpe: null,
			date_fin_validite_dpe: null
		}
	],
	total: 3,
	distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 2 },
	page_size: 2,
	has_more: true,
	next_cursor: 'synthetic-next-cursor',
	read_state: 'partial',
	association: 'source_ban_equality',
	address_check: 'unverified',
	source: 'ADEME',
	source_coverage: 'unknown',
	graph_state: 'not_checked',
	imported_at: null
};

const comparison = {
	inventory,
	attribution: {
		version: 2,
		status: 'unassigned',
		reason: 'dwelling_identity_unproven',
		diagnostic: null,
		candidates: [],
		building_context: { count: 3, distribution: { C: 1 }, truncated: true },
		graph_state: 'not_checked'
	},
	rule_version: 'dpe-factual-comparison-v1',
	comparisons: [
		{
			document_ref: PRIVATE_DOCUMENT_REF,
			criteria: [
				{ criterion: 'type_bien', state: 'matching', target: 'appartement', source: 'appartement' },
				{ criterion: 'surface', state: 'known', target: 50, source: 48, target_nature: 'habitable_dpe', source_nature: 'habitable_dpe', delta: -2 }
			],
			blockers: []
		},
		{
			document_ref: 'opaque-private-doc-beta',
			criteria: [
				{ criterion: 'type_bien', state: 'unknown', target: 'appartement', source: null },
				{ criterion: 'surface', state: 'unknown', target: 50, source: null, target_nature: 'habitable_dpe', source_nature: 'habitable_dpe', delta: null }
			],
			blockers: ['validity_unknown']
		}
	],
	limits: ['dwelling_identity_unproven', 'inventory_partial', 'candidates_outside_page']
};

async function mockIdentity(page: Page, admin: boolean): Promise<void> {
	await page.route('**/auth/me', route => route.fulfill({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify(admin ? ADMIN : NON_ADMIN)
	}));
}

// 04-12: the address is searched by this instance's field (`/v1/adresses/recherche`), never by the
// former geocoder client; the resolver still receives the « {n°} {voie}, {CP} {Commune} » label.
async function mockAddressContext(page: Page): Promise<{ geocodeCalls: () => number; resolveQueries: string[] }> {
	let geocode = 0;
	const resolveQueries: string[] = [];
	await page.route('**/v1/geocode?*', route => {
		geocode += 1;
		return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
	});
	await page.route('**/v1/adresses/recherche?*', route => route.fulfill({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify({ statut: 'ok', donnees: {
			ordre_types: ['adresse', 'commune'],
			resultats: [{
				type_resultat: 'adresse', ban_id: '44109_9999_00007', numero: '7', rep: null, nom_voie: 'rue Synthétique',
				code_postal: '44000', code_commune: '44109', nom_commune: 'Nantes', departement: { code: '44', nom: 'Loire-Atlantique' }
			}],
			hebergement: { ban: { etat: 'heberge', motif: null, departements: ['44', '85'], territoire: null, homonymes: [] } }
		} })
	}));
	await page.route('**/v1/resolve?*', route => {
		resolveQueries.push(new URL(route.request().url()).searchParams.get('q') ?? '');
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ statut: 'ok', donnees: [{
				type_detecte: 'adresse', latitude: 47.21, longitude: -1.55, adresse: ADDRESS_LABEL,
				id_parcelle: null, commune: 'Nantes', code_postal: '44000', score: 1,
				dpe_inventory: inventory, datasets: {}, erreurs: [], avertissements: []
			}] })
		});
	});
	await page.route('**/v1/bien/dpe/inventaire*', route => route.fulfill({
		status: 200, contentType: 'application/json', body: JSON.stringify(inventory)
	}));
	return { geocodeCalls: () => geocode, resolveQueries };
}

async function selectSyntheticAddress(page: Page): Promise<void> {
	const input = page.getByRole('combobox', { name: 'Adresse', exact: true });
	await input.fill('rue Synthétique');
	await page.getByRole('listbox', { name: 'Adresses proposées' }).getByRole('option', { name: /7 rue Synthétique/ }).click();
	await expect(page.getByText(`Adresse sélectionnée : ${ADDRESS_LABEL}`)).toBeVisible();
	await expect(page).toHaveURL(/\/admin\/dpe-matching-preview$/);
	await expect(page.getByRole('button', { name: 'Comparer les documents' })).toBeEnabled();
}

test.describe('Admin — aperçu privé de comparaison DPE', () => {
	for (const width of [320, 375, 1280]) {
	test(`un admin compare tous les candidats sans attribution ni débordement à ${width}px`, async ({ page }, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		await installLocalNetworkGuard(page);
		await mockIdentity(page, true);
		const address = await mockAddressContext(page);

		let releaseComparison!: () => void;
		const comparisonReleased = new Promise<void>(resolve => { releaseComparison = resolve; });
		let postedUrl = '';
		let postedBody: unknown;
		await page.route('**/v1/bien/dpe/comparaison', async route => {
			postedUrl = route.request().url();
			postedBody = route.request().postDataJSON();
			await comparisonReleased;
			await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(comparison) });
		});

		const consoleMessages: string[] = [];
		page.on('console', message => consoleMessages.push(message.text()));
		await page.goto('/admin/dpe-matching-preview');
		await expect(page.getByRole('heading', { name: 'Comparaison factuelle DPE' })).toBeVisible();
		await selectSyntheticAddress(page);
		await page.getByRole('combobox', { name: 'Type de bien' }).selectOption('appartement');
		await page.getByRole('spinbutton', { name: 'Surface' }).fill('50');
		await page.getByRole('button', { name: 'Comparer les documents' }).click();
		await expect(page.getByText('Chargement…', { exact: true })).toBeVisible();
		releaseComparison();

		const table = page.getByRole('table', { name: 'Comparaison de tous les documents de la page' });
		await expect(table).toBeVisible();
		await expect(table.getByRole('row')).toHaveCount(3);
		await expect(page.getByText('L’identité du logement reste non prouvée.')).toBeVisible();
		await expect(page.getByText('Inventaire partiel.')).toBeVisible();
		await expect(page.getByText('dpe-factual-comparison-v1')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Page suivante' })).toBeVisible();
		await expect(page.getByText(/DPE de votre logement/i)).toHaveCount(0);
		await expect(page.getByText(/diagnostic attribué/i)).toHaveCount(0);

		expect(address.resolveQueries).toEqual([ADDRESS_LABEL]);
		expect(address.geocodeCalls()).toBe(0);
		expect(new URL(postedUrl).search).toBe('');
		expect(postedBody).toEqual({
			contexte: 'synthetic-context-preview',
			limite: 20,
			cible: { type_bien: 'appartement', surface: { value: 50, nature: 'habitable_dpe' } }
		});
		expect(await page.locator('body').innerText()).not.toContain(PRIVATE_DOCUMENT_REF);
		expect(page.url()).not.toContain(PRIVATE_DOCUMENT_REF);
		expect(consoleMessages.join('\n')).not.toContain(PRIVATE_DOCUMENT_REF);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		expectNoUnexpectedOffLocalRequest(page);
		await testInfo.attach(`dpe-matching-preview-${width}px`, {
			body: await page.screenshot({ fullPage: true }), contentType: 'image/png'
		});
	});
	}

	test('une panne de comparaison reste une erreur et ne devient pas une absence', async ({ page }) => {
		await installLocalNetworkGuard(page);
		await mockIdentity(page, true);
		await mockAddressContext(page);
		await page.route('**/v1/bien/dpe/comparaison', route => route.fulfill({
			status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'synthetic upstream failure' })
		}));

		await page.goto('/admin/dpe-matching-preview');
		await selectSyntheticAddress(page);
		await page.getByRole('button', { name: 'Comparer les documents' }).click();
		await expect(page.getByRole('alert')).toContainText('momentanément indisponible');
		await expect(page.getByRole('alert')).toContainText('ne constitue pas une absence');
		await expect(page.getByText(/aucun diagnostic/i)).toHaveCount(0);
		expectNoUnexpectedOffLocalRequest(page);
	});

	test('un non-admin est refusé par le layout réel avant affichage de l’aperçu', async ({ page }) => {
		await installLocalNetworkGuard(page);
		await mockIdentity(page, false);
		await page.goto('/admin/dpe-matching-preview');
		await expect(page).toHaveURL(/\/\?admin_required=1/, { timeout: 10_000 });
		await expect(page.getByRole('heading', { name: 'Comparaison factuelle DPE' })).toHaveCount(0);
		expectNoUnexpectedOffLocalRequest(page);
	});
});
